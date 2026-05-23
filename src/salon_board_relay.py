"""Salon Board リレークライアント.

GitHub Actions（米国 IP）から salonboard.com への POST が Akamai に
silent drop されるため、お名前.com レンタルサーバに置いた PHP リレー
（Japan IP）経由で HTTP リクエストを送る。

リレーは generic な HTTP forwarder で、Python 側で URL/method/data を組み立てて
JSON で送信し、PHP 側で curl 実行 + クッキー管理 + レスポンス返却を行う。
"""
from __future__ import annotations

import base64
import logging
import os
import re
from dataclasses import dataclass, field
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


def is_logged_in_after(relay: SalonBoardRelay, probe_url: str = "https://salonboard.com/KLP/blog/blog/") -> bool:
    """ログイン後、保護領域にアクセスできるか確認。

    認証成功なら sc_data に storeid が入っている。
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

    storeid, userid = _parse_login_state(r.body_text)
    if storeid:
        log.info("✅ Probe confirms authenticated state: storeid=%s", storeid)
        return True
    log.warning(
        "Probe body has no storeid in sc_data → likely not authenticated. Title hint: %s",
        (re.search(r"<title>([^<]+)</title>", r.body_text) or [None, "(no title)"])[1]
        if re.search(r"<title>([^<]+)</title>", r.body_text) else "(no title)",
    )
    return False
