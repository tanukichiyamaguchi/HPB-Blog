"""Salon Board リレークライアント.

GitHub Actions（米国 IP）から salonboard.com への POST が Akamai に
silent drop されるため、お名前.com レンタルサーバに置いた PHP リレー
（Japan IP）経由で HTTP リクエストを送る。

リレーは generic な HTTP forwarder で、Python 側で URL/method/data を組み立てて
JSON で送信し、PHP 側で curl 実行 + クッキー管理 + レスポンス返却を行う。
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

log = logging.getLogger(__name__)


# ----- env -----


def get_relay_config() -> tuple[str, str]:
    """RELAY_URL と RELAY_SECRET を環境変数から取得。

    どちらも未設定なら RuntimeError を投げる。
    """
    url = os.environ.get("RELAY_URL", "").strip()
    secret = os.environ.get("RELAY_SECRET", "").strip()
    if not url or not secret:
        raise RuntimeError(
            "RELAY_URL and RELAY_SECRET must both be set (env or GitHub Secrets). "
            "See relay/README.md for setup instructions."
        )
    return url, secret


# ----- low-level client -----


@dataclass
class RelayResponse:
    """relay.php からの JSON レスポンスを構造化したもの。"""
    status: int
    final_url: str
    headers_text: str
    body_bytes: bytes
    cookies: dict[str, str]
    elapsed_seconds: float

    @property
    def body_text(self) -> str:
        return self.body_bytes.decode("utf-8", errors="replace")

    def header(self, name: str) -> str | None:
        """Case-insensitive ヘッダ取得。"""
        target = name.lower() + ":"
        for line in self.headers_text.split("\n"):
            if line.lower().startswith(target):
                return line.split(":", 1)[1].strip()
        return None

    @property
    def location(self) -> str:
        return self.header("Location") or self.header("location") or ""


class SalonBoardRelay:
    """お名前.com PHP リレーを経由する HTTP クライアント.

    Usage:
        relay = SalonBoardRelay(url, secret)
        # GET (Akamai 通過)
        r = relay.get("https://salonboard.com/login/")
        # POST (これが GitHub Actions の本当の課題。リレー経由なら通る)
        r = relay.post(
            "https://salonboard.com/CNC/login/doLogin/",
            data={"userId": uid, "password": pw},
            headers={"Referer": "https://salonboard.com/login/"},
        )
        # マルチパート (画像アップロード等)
        r = relay.post(
            blog_url,
            data={"title": "..."},
            files={"image": {
                "content": base64.b64encode(image_bytes).decode(),
                "filename": "image.jpg",
                "mime": "image/jpeg",
            }},
        )
    """

    def __init__(self, relay_url: str, relay_secret: str, request_timeout: int = 60):
        if not relay_url.startswith("https://"):
            raise ValueError(f"relay_url must be HTTPS: {relay_url}")
        self.relay_url = relay_url
        self.relay_secret = relay_secret
        self.request_timeout = request_timeout
        self.session_id: str | None = None
        self.cookies: dict[str, str] = {}

    def _call(
        self,
        method: str,
        url: str,
        *,
        data: dict[str, str] | str | None = None,
        files: dict[str, dict[str, str]] | None = None,
        headers: dict[str, str] | None = None,
        timeout: int = 30,
        follow_redirects: bool = False,
    ) -> RelayResponse:
        payload: dict[str, Any] = {
            "method": method.upper(),
            "url": url,
            "session_id": self.session_id or "",
            "data": data,
            "headers": headers or {},
            "files": files,
            "timeout": timeout,
            "follow_redirects": follow_redirects,
        }
        log.debug("Relay → %s %s (session=%s)", method, url, self.session_id or "<new>")
        try:
            resp = requests.post(
                self.relay_url,
                json=payload,
                headers={"X-RELAY-SECRET": self.relay_secret},
                timeout=self.request_timeout,
            )
        except requests.RequestException as e:
            raise RuntimeError(f"Relay request failed (network): {e}") from e

        if resp.status_code != 200:
            raise RuntimeError(
                f"Relay returned HTTP {resp.status_code}: {resp.text[:300]}"
            )

        try:
            result = resp.json()
        except ValueError as e:
            raise RuntimeError(f"Relay returned non-JSON: {resp.text[:300]}") from e

        if not result.get("ok"):
            raise RuntimeError(f"Relay error: {result.get('error', 'unknown')}")

        # session/cookie の永続化
        sid = result.get("session_id")
        if sid:
            self.session_id = sid
        if result.get("cookies"):
            self.cookies.update(result["cookies"])

        body_b64 = result.get("body_base64", "")
        body_bytes = base64.b64decode(body_b64) if body_b64 else b""

        relay_response = RelayResponse(
            status=int(result.get("status", 0)),
            final_url=result.get("final_url", ""),
            headers_text=result.get("headers", ""),
            body_bytes=body_bytes,
            cookies=dict(result.get("cookies", {})),
            elapsed_seconds=float(result.get("elapsed_seconds", 0.0)),
        )
        log.debug(
            "Relay ← HTTP %d, %d bytes, %.2fs",
            relay_response.status, len(body_bytes), relay_response.elapsed_seconds,
        )
        return relay_response

    def get(
        self, url: str, *,
        headers: dict[str, str] | None = None,
        timeout: int = 30,
        follow_redirects: bool = False,
    ) -> RelayResponse:
        return self._call("GET", url, headers=headers, timeout=timeout,
                          follow_redirects=follow_redirects)

    def post(
        self, url: str, *,
        data: dict[str, str] | str | None = None,
        files: dict[str, dict[str, str]] | None = None,
        headers: dict[str, str] | None = None,
        timeout: int = 30,
        follow_redirects: bool = False,
    ) -> RelayResponse:
        return self._call("POST", url, data=data, files=files, headers=headers,
                          timeout=timeout, follow_redirects=follow_redirects)


# ----- high-level operations -----


SALON_BOARD_LOGIN_URL = "https://salonboard.com/login/"
SALON_BOARD_LOGIN_POST_URL = "https://salonboard.com/CNC/login/doLogin/"
# Blog new/edit input page. GET to retrieve the form with CSRF tokens etc.
# POST to submit the form (proceeds to the confirmation step).
SALON_BOARD_BLOG_NEW_URL = "https://salonboard.com/KLP/blog/blog/"
# Image upload endpoint (discovered via DevTools Network tab):
#   POST multipart/form-data, single field "formFile"=<binary>
#   Response JSON: {"imagePath": "https://imgbp.salonboard.com/..."}
SALON_BOARD_IMAGE_UPLOAD_URL = "https://salonboard.com/KLP/blog/blogImageAjax/doUpload"
# Confirm endpoint (POST blog form fields → returns confirmation page with new CSRF)
SALON_BOARD_BLOG_CONFIRM_URL = "https://salonboard.com/KLP/blog/blog/confirm"
# Final reflect endpoint (POST new CSRF + storeIdForMultipleTabCheck → 302 to /complete).
# Discovered: the "登録・反映予約する" button (id="reflect") JS posts here with just
# 2 fields: the new TOKEN from the confirm response + the unchanged storeIdForMultipleTabCheck.
SALON_BOARD_BLOG_REFLECT_URL = "https://salonboard.com/KLP/blog/blog/doReflectComplete"


# Staff IDs (verified from the blog edit form select options)
STAFF_IDS = {
    "momo": "W001361894",
    "aoi": "W001363601",
    # pome: "W001414182" (非掲載 — auto-excluded by Salon Board)
    "ケイト 蒲田西口店": "F000773652",
}

# Blog category codes (verified from the blog edit form select options)
BLOG_CATEGORY_CODES = {
    "プライベート": "KL01",
    "サロンのNEWS": "KL02",
    "おすすめメニュー": "KL03",  # default for our automation
    "おすすめデザイン": "KL04",
    "ビューティー": "KL05",
}

# Staff IDs that should be excluded from the public-facing staff dropdown
# (= 非掲載 staff). Salon Board's blog form requires this list to be POSTed
# in the unPublishStaff hidden field — otherwise non-published staff may
# accidentally re-appear as selectable authors.
# Captured value: "W001414182," (single id + trailing comma).
UNPUBLISH_STAFF_IDS: list[str] = [
    "W001414182",  # pome (non-publishing)
]


# Salon Board は post-login ページに JavaScript 変数 sc_data を inline で埋め込んで、
# その中に userid と storeid を含める。認証成功なら storeid に実店舗 ID が入る。
#   未認証: sc_data = { ..., userid : '', storeid : '', ... }
#   認証済: sc_data = { ..., userid : 'CE12345', storeid : 'H000797013', ... }
_STOREID_RE = re.compile(r"""storeid\s*:\s*['"]([^'"]*)['"]""")
_USERID_RE = re.compile(r"""userid\s*:\s*['"]([^'"]*)['"]""")


def _parse_login_state(body: str) -> tuple[str, str]:
    """Return (storeid, userid) extracted from sc_data inline JS in the body.

    Either is empty string if not found / not authenticated.
    """
    sid = _STOREID_RE.search(body)
    uid = _USERID_RE.search(body)
    return (
        sid.group(1).strip() if sid else "",
        uid.group(1).strip() if uid else "",
    )


def login(relay: SalonBoardRelay, user_id: str, password: str) -> RelayResponse:
    """salonboard.com にリレー経由でログインする.

    Salon Board は HTTP 302 redirect ではなく HTTP 200 で post-login ページを直接
    返す設計。認証判定は応答ボディ内 ``sc_data`` の ``storeid`` が非空かどうかで行う。

    Returns:
        RelayResponse: POST /CNC/login/doLogin/ のレスポンス

    Raises:
        RuntimeError: 認証失敗（storeid が空 or /login にリダイレクト等）
    """
    # 1. GET login page (Akamai cookies の seed)
    log.info("Relay login: GET %s", SALON_BOARD_LOGIN_URL)
    r = relay.get(SALON_BOARD_LOGIN_URL, headers={
        "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.5",
    })
    log.info(
        "  → HTTP %d (%d bytes, %.2fs), cookies: %s",
        r.status, len(r.body_bytes), r.elapsed_seconds, list(r.cookies.keys()),
    )
    if r.status != 200:
        raise RuntimeError(f"Login GET failed: HTTP {r.status}")
    if "ログイン" not in r.body_text:
        log.warning("Login GET body doesn't contain ログイン; might not be login page")

    # 2. POST credentials
    log.info("Relay login: POST %s", SALON_BOARD_LOGIN_POST_URL)
    r = relay.post(
        SALON_BOARD_LOGIN_POST_URL,
        data={"userId": user_id, "password": password},
        headers={
            "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.5",
            "Origin": "https://salonboard.com",
            "Referer": SALON_BOARD_LOGIN_URL,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        follow_redirects=False,
    )
    log.info(
        "  → HTTP %d, location=%r, %.2fs, cookies now: %s",
        r.status, r.location, r.elapsed_seconds, list(relay.cookies.keys()),
    )

    # 認証判定 1: 302 redirect で /login 以外への遷移
    if r.status in (301, 302, 303):
        if r.location and "login" not in r.location.lower():
            log.info("✅ Login successful via redirect to: %s", r.location)
            return r
        raise RuntimeError(f"Login failed: redirected back to {r.location!r}")

    # 認証判定 2: HTTP 200 で body の sc_data に storeid が入っているかをチェック
    # Salon Board は redirect せず post-login ページを直接返してくる
    if r.status == 200:
        storeid, userid = _parse_login_state(r.body_text)
        if storeid:
            log.info(
                "✅ Login successful (HTTP 200 with sc_data): storeid=%s, userid=%s",
                storeid, "<masked>" if userid else "<empty>",
            )
            return r
        # storeid 空 = ログインフォーム再表示 (認証失敗)
        body_preview = r.body_text[:400].replace("\n", " ")
        raise RuntimeError(
            f"Login failed: HTTP 200 but storeid empty (credentials likely wrong). "
            f"Body preview: {body_preview}"
        )

    raise RuntimeError(f"Login: unexpected status {r.status}")


# 認証されていない場合に <title> に出現する文字列。
# 認証済みページでは "SALON BOARD : ブログ編集 入力" や "SALON BOARD : 予約一覧" のように
# 機能名が入る。これらは「未認証」または「エラー」を示すタイトル。
_UNAUTH_TITLE_KEYWORDS = (
    "ログイン",        # /login/ の login page
    "エラー",          # SALON BOARD : エラー (URLなし、セッション切れ等)
    "Session",         # tomcat 系セッション切れ
)


def upload_image(
    relay: SalonBoardRelay,
    image_path: Path | str,
    mime: str | None = None,
) -> str:
    """Salon Board に画像をアップロードして imagePath URL を取得する.

    Discovered endpoint (DevTools Network capture):
      POST https://salonboard.com/KLP/blog/blogImageAjax/doUpload
      multipart/form-data with single field "formFile" = <image bytes>
      Response: {"imagePath": "https://imgbp.salonboard.com/..."}

    Args:
        relay: ログイン済の SalonBoardRelay
        image_path: アップロードする画像ファイルパス
        mime: MIME type (推測されない場合のみ指定; image/jpeg, image/png 等)

    Returns:
        imagePath URL (フォームの imagePath1〜4 hidden field にセットする値)
    """
    image_path = Path(image_path)
    if not image_path.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")

    # MIME type の決定
    if mime is None:
        ext = image_path.suffix.lower()
        mime = {
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
            ".gif": "image/gif",
        }.get(ext, "application/octet-stream")

    image_bytes = image_path.read_bytes()
    log.info(
        "Uploading image: %s (%d bytes, %s)",
        image_path.name, len(image_bytes), mime,
    )

    r = relay.post(
        SALON_BOARD_IMAGE_UPLOAD_URL,
        files={
            "formFile": {
                "content": base64.b64encode(image_bytes).decode("ascii"),
                "filename": image_path.name,
                "mime": mime,
            },
        },
        headers={
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": "https://salonboard.com",
            "Referer": SALON_BOARD_BLOG_NEW_URL,
        },
        timeout=60,
        follow_redirects=False,
    )

    log.info(
        "  → HTTP %d, %d bytes, %.2fs",
        r.status, len(r.body_bytes), r.elapsed_seconds,
    )

    if r.status != 200:
        body_preview = r.body_text[:300].replace("\n", " ")
        raise RuntimeError(
            f"Image upload failed: HTTP {r.status}. Body: {body_preview}"
        )

    try:
        result = json.loads(r.body_text)
    except json.JSONDecodeError as e:
        body_preview = r.body_text[:300].replace("\n", " ")
        raise RuntimeError(
            f"Image upload returned non-JSON: {body_preview}"
        ) from e

    image_url = result.get("imagePath")
    if not image_url or not isinstance(image_url, str):
        raise RuntimeError(f"Image upload response has no imagePath: {result}")

    log.info("✅ Image uploaded: %s", image_url)
    return image_url


def is_logged_in_after(
    relay: SalonBoardRelay,
    probe_url: str = "https://salonboard.com/KLP/blog/blog/",
) -> bool:
    """ログイン後、保護領域にアクセスできるか確認。

    判定方法（信頼度の高い順）:
      1. 200 以外 / login URL への redirect → ❌ 未認証
      2. <title> に「ログイン」「エラー」等のキーワード → ❌ 未認証
      3. body にログインフォーム（id=idPasswordInputForm 等）が含まれる → ❌ 未認証
      4. それ以外（保護領域のページが返っている）→ ✅ 認証済
    """
    log.info("Auth probe: GET %s", probe_url)
    try:
        r = relay.get(probe_url, follow_redirects=False)
    except Exception as e:
        log.warning("Auth probe failed: %s", e)
        return False
    log.info("  → HTTP %d, location=%r, body=%d bytes",
             r.status, r.location, len(r.body_bytes))

    if r.status in (301, 302) and "login" in (r.location or "").lower():
        log.warning("Probe redirected to login → not authenticated")
        return False
    if r.status != 200:
        log.warning("Probe returned non-200 status → likely not authenticated")
        return False

    body = r.body_text
    title_match = re.search(r"<title>([^<]+)</title>", body)
    title = title_match.group(1).strip() if title_match else "(no title)"

    # 未認証を示すタイトル
    for kw in _UNAUTH_TITLE_KEYWORDS:
        if kw in title:
            log.warning(
                "Probe title indicates unauthenticated/error state: %r (matched %r)",
                title, kw,
            )
            return False

    # body にログインフォームの構造が含まれていれば未認証
    if 'idPasswordInputForm' in body or (
        'name="userId"' in body and 'name="password"' in body
    ):
        log.warning("Probe body contains login form structure → unauthenticated")
        return False

    log.info("✅ Probe confirms authenticated state: title=%r", title)
    return True


# ----- blog submission (input → confirm → reflect) -----


# Struts CSRF token: hidden <input> appearing on every form-bearing page.
# The value changes on every render — the confirm response carries a *new* token
# that doReflectComplete requires.
_CSRF_RE = re.compile(
    r'<input[^>]*name="org\.apache\.struts\.taglib\.html\.TOKEN"[^>]*value="([^"]+)"',
    re.IGNORECASE,
)
# Per-tab nonce that Salon Board uses to detect "form opened in multiple tabs"
# situations. Unchanged across the input → confirm → reflect chain.
_STORE_TAB_RE = re.compile(
    r'<input[^>]*name="storeIdForMultipleTabCheck"[^>]*value="([^"]+)"',
    re.IGNORECASE,
)


def _parse_form_tokens(body: str) -> tuple[str, str]:
    """Return (csrf_token, storeIdForMultipleTabCheck) from form HTML.

    Raises RuntimeError if either field is missing.
    """
    csrf = _CSRF_RE.search(body)
    tab = _STORE_TAB_RE.search(body)
    if not csrf or not tab:
        raise RuntimeError(
            "Failed to parse form tokens "
            f"(csrf_found={bool(csrf)}, tab_found={bool(tab)})"
        )
    return csrf.group(1), tab.group(1)


def submit_blog(
    relay: SalonBoardRelay,
    *,
    title: str,
    body: str,
    staff_id: str,
    reserve_dt: datetime,
    category_code: str = "KL03",
    image_paths: list[Path | str] | None = None,
    unpublish_staff_ids: list[str] | None = None,
) -> str:
    """ブログ記事を Salon Board に投稿する（予約投稿）.

    フロー (DevTools キャプチャで判明):
      1. GET  /KLP/blog/blog/                  → CSRF + storeIdForMultipleTabCheck
      2. POST /KLP/blog/blogImageAjax/doUpload → 画像ごとに imagePath を取得
      3. POST /KLP/blog/blog/confirm           → 確認画面 + 新 CSRF
      4. POST /KLP/blog/blog/doReflectComplete → 302 → /KLP/blog/blog/complete

    Args:
        relay: 既にログイン済の SalonBoardRelay。
        title: タイトル（必須）。
        body: 本文の HTML（nicEdit 互換: <p>, <br>, <strong>, <img> 等が使える）。
        staff_id: 投稿スタッフ ID。``STAFF_IDS`` の値から1つを渡す。
        reserve_dt: 予約投稿日時。tz-naive でも tz-aware でも構わないが、
            Salon Board は JST で扱うので JST のローカル時刻として送信する。
            分以上の精度は送信されず分単位で 00 にされるわけではなく、
            HHMM そのまま送る（実際の予約スロットは 00分/15分/30分/45分のみ）。
        category_code: ``BLOG_CATEGORY_CODES`` の値。デフォルトは "おすすめメニュー" (KL03)。
        image_paths: 最大4枚までの画像ファイルパス。None / 空なら画像なし投稿。
        unpublish_staff_ids: 非掲載スタッフ ID リスト。None なら
            モジュール定数 ``UNPUBLISH_STAFF_IDS`` を使う。

    Returns:
        完了画面の URL (``https://salonboard.com/KLP/blog/blog/complete``)。

    Raises:
        ValueError: image_paths が4枚を超える場合。
        RuntimeError: いずれかのステップで HTTP / フォーム検証エラー。
    """
    images = list(image_paths or [])
    if len(images) > 4:
        raise ValueError(f"Salon Board blog accepts up to 4 images; got {len(images)}")

    unpublish_list = (
        list(unpublish_staff_ids)
        if unpublish_staff_ids is not None
        else list(UNPUBLISH_STAFF_IDS)
    )
    # 観測された送信値は "W001414182," のように末尾カンマ込み。空なら空文字。
    unpublish_field = (",".join(unpublish_list) + ",") if unpublish_list else ""

    # ----- Step 1: GET input form (CSRF + storeIdForMultipleTabCheck) -----
    log.info("Submit blog [1/4]: GET %s", SALON_BOARD_BLOG_NEW_URL)
    r = relay.get(
        SALON_BOARD_BLOG_NEW_URL,
        headers={"Accept-Language": "ja-JP,ja;q=0.9,en;q=0.5"},
    )
    if r.status != 200:
        raise RuntimeError(f"Blog form load failed: HTTP {r.status}")
    csrf_in, tab_in = _parse_form_tokens(r.body_text)
    log.info(
        "  Initial form tokens: csrf=%s..., tab=%s...",
        csrf_in[:8], tab_in[:8],
    )

    # ----- Step 2: Upload images sequentially -----
    image_urls: list[str] = []
    for idx, img in enumerate(images, start=1):
        log.info("Submit blog [2/4]: upload image %d/%d", idx, len(images))
        image_urls.append(upload_image(relay, img))

    # ----- Step 3: POST confirm -----
    rsv_date = reserve_dt.strftime("%Y%m%d")
    rsv_time = reserve_dt.strftime("%H%M")

    confirm_data: dict[str, str] = {
        "org.apache.struts.taglib.html.TOKEN": csrf_in,
        "storeIdForMultipleTabCheck": tab_in,
        "blogContents1": body,
        "blogContents2": "",
        "blogContents3": "",
        "blogContents4": "",
        "blogContents5": "",
        "imagePath1": image_urls[0] if len(image_urls) >= 1 else "",
        "imagePath2": image_urls[1] if len(image_urls) >= 2 else "",
        "imagePath3": image_urls[2] if len(image_urls) >= 3 else "",
        "imagePath4": image_urls[3] if len(image_urls) >= 4 else "",
        "staffId": staff_id,
        "unPublishStaff": unpublish_field,
        "blogCategoryCd": category_code,
        "title": title,
        "rsvTokoFlg": "1",
        "rsvTokoDate": rsv_date,
        "rsvTokoTime": rsv_time,
    }

    log.info(
        "Submit blog [3/4]: POST %s (title=%r, staff=%s, cat=%s, rsv=%s %s, imgs=%d)",
        SALON_BOARD_BLOG_CONFIRM_URL,
        title[:30], staff_id, category_code, rsv_date, rsv_time, len(image_urls),
    )
    r = relay.post(
        SALON_BOARD_BLOG_CONFIRM_URL,
        data=confirm_data,
        headers={
            "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.5",
            "Origin": "https://salonboard.com",
            "Referer": SALON_BOARD_BLOG_NEW_URL,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        follow_redirects=False,
    )
    if r.status != 200:
        preview = r.body_text[:400].replace("\n", " ")
        raise RuntimeError(f"Confirm failed: HTTP {r.status}. Body: {preview}")

    # 確認画面に「修正」「戻る」が出ていなければバリデーションエラー画面の可能性
    if "id=\"reflect\"" not in r.body_text and "reflect" not in r.body_text.lower():
        # エラーメッセージ抽出（class="error" や赤字 div 想定; 取れなければ本文先頭）
        err_match = re.search(
            r'<(?:span|div|p)[^>]*(?:class|id)="[^"]*err[^"]*"[^>]*>([^<]+)',
            r.body_text, re.IGNORECASE,
        )
        msg = err_match.group(1).strip() if err_match else r.body_text[:300]
        raise RuntimeError(f"Confirm rejected the form: {msg}")

    csrf_new, tab_new = _parse_form_tokens(r.body_text)
    log.info(
        "  Confirm OK; new tokens: csrf=%s..., tab=%s...",
        csrf_new[:8], tab_new[:8],
    )
    if tab_new != tab_in:
        log.warning(
            "storeIdForMultipleTabCheck changed across confirm (%s → %s); proceeding with new value",
            tab_in[:8], tab_new[:8],
        )

    # ----- Step 4: POST doReflectComplete (final submit) -----
    log.info("Submit blog [4/4]: POST %s", SALON_BOARD_BLOG_REFLECT_URL)
    r = relay.post(
        SALON_BOARD_BLOG_REFLECT_URL,
        data={
            "org.apache.struts.taglib.html.TOKEN": csrf_new,
            "storeIdForMultipleTabCheck": tab_new,
        },
        headers={
            "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.5",
            "Origin": "https://salonboard.com",
            "Referer": SALON_BOARD_BLOG_CONFIRM_URL,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        follow_redirects=False,
    )

    if r.status != 302:
        preview = r.body_text[:400].replace("\n", " ")
        raise RuntimeError(
            f"doReflectComplete unexpected status: HTTP {r.status}. Body: {preview}"
        )
    if "complete" not in (r.location or "").lower():
        raise RuntimeError(
            f"doReflectComplete redirected to unexpected location: {r.location!r}"
        )

    log.info("✅ Blog submitted; redirect → %s", r.location)
    return r.location
