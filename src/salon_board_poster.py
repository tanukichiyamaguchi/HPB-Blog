"""Salon Board automation via Playwright.

Real flow (inspected from user screenshots 2026-05-23):
1. https://salonboard.com/login/  → ID + PW + ログイン
2. https://salonboard.com/KLP/blog/blog/  (direct URL, no menu nav needed)
3. Form fields on the new-post page:
   - 投稿者 (select): rotates by date through momo / aoi / ケイト 蒲田西口店
   - カテゴリ (select): default おすすめメニュー
   - タイトル (input): 全角25文字以下
   - 本文 (textarea): 全角1000文字以下
   - 画像アップロード (button → modal → file input → 登録する)
   - クーポン (button → modal, optional — TODO for Phase 4 polish)
   - 予約投稿 (radio: 設定しない | 設定する + date/time pickers)
4. 確認する → preview page
5. On preview: 下書き保存 (Phase 3) or 予約する (Phase 5)

Selectors carry multiple fallbacks; each step writes a screenshot to
``screenshots/`` so failures can be diagnosed and refined iteratively.

GitHub Actions hardening:
- HTTP pre-flight to log whether salonboard.com is reachable at all
- Anti-bot-detection browser flags / user-agent / navigator.webdriver hiding
- Progressive page-load strategy: domcontentloaded → commit → wait_for_selector
- Bounded screenshot timeout with viewport fallback
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Sequence

import requests
from playwright.sync_api import (
    Page,
    sync_playwright,
)
from playwright.sync_api import TimeoutError as PWTimeout

from src.config import SCREENSHOTS_DIR

log = logging.getLogger(__name__)


def _env_url(name: str, default: str) -> str:
    return os.environ.get(name, default).strip() or default


SALON_BOARD_LOGIN_URL = _env_url(
    "SALON_BOARD_LOGIN_URL",
    "https://salonboard.com/login/",
)
SALON_BOARD_BLOG_NEW_URL = _env_url(
    "SALON_BOARD_BLOG_NEW_URL",
    "https://salonboard.com/KLP/blog/blog/",
)

DEFAULT_TIMEOUT_MS = int(os.environ.get("SB_TIMEOUT_MS", "30000"))
NAVIGATION_TIMEOUT_MS = int(os.environ.get("SB_NAV_TIMEOUT_MS", "90000"))
PER_SELECTOR_TIMEOUT_MS = int(os.environ.get("SB_PER_SELECTOR_TIMEOUT_MS", "2500"))
SCREENSHOT_TIMEOUT_MS = int(os.environ.get("SB_SCREENSHOT_TIMEOUT_MS", "15000"))

# Firefox UA. Critical: Akamai on salonboard.com silently drops requests with a
# Chromium TLS fingerprint (verified Jan 2026: chromium/chrome131 → timeout;
# firefox133/safari17 → HTTP 200). Pairing a Firefox UA with the Firefox engine
# keeps the UA / fingerprint consistent — overriding to a Chrome UA on a Firefox
# engine would create an inconsistency that some WAFs flag.
DEFAULT_USER_AGENT = os.environ.get(
    "SB_USER_AGENT",
    "Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0",
)


# Daily poster rotation (one per day). pome(非掲載) is intentionally excluded.
POSTER_ROTATION: tuple[str, ...] = (
    "momo",
    "aoi",
    "ケイト 蒲田西口店",
)

# Salon Board category options (visible labels):
#   プライベート / サロンのNEWS / おすすめメニュー / おすすめデザイン / ビューティー
# Our blog is always menu-focused → use おすすめメニュー by default.
DEFAULT_CATEGORY = "おすすめメニュー"


def get_poster_for_date(d: date) -> str:
    """Return the rotating poster name for the given date (deterministic)."""
    return POSTER_ROTATION[d.toordinal() % len(POSTER_ROTATION)]


# --- Selector candidate sets (order = priority) ----------------------------- #

LOGIN_ID_SELECTORS: tuple[str, ...] = (
    "#idPasswordInputId",
    "input[name='userId']",
    "input[name='loginId']",
    "input[name='id']",
    "input[type='text']:visible",
)
LOGIN_PW_SELECTORS: tuple[str, ...] = (
    "#idPasswordInputPassword",
    "input[name='password']",
    "input[type='password']:visible",
)
LOGIN_SUBMIT_SELECTORS: tuple[str, ...] = (
    # Verified via HTML dump (2026-01): the login button is:
    #   <div class="loginBtnWrap">
    #     <a href="javascript:void(0);"
    #        class="common-CNCcommon__primaryBtn loginBtnSize"
    #        onclick="dologin(event); return false;">ログイン</a>
    #   </div>
    "div.loginBtnWrap a.common-CNCcommon__primaryBtn",
    "a.common-CNCcommon__primaryBtn.loginBtnSize",
    "a[onclick*='dologin']",
    "form a.common-CNCcommon__primaryBtn",
    "a.common-CNCcommon__primaryBtn:has-text('ログイン')",
)

POSTER_SELECT_SELECTORS: tuple[str, ...] = (
    "select[name='posterId']",
    "select[name='poster']",
    "select[name='blogPoster']",
    "select[name='posterCd']",
)
CATEGORY_SELECT_SELECTORS: tuple[str, ...] = (
    "select[name='category']",
    "select[name='categoryCd']",
    "select[name='blogCategory']",
)
TITLE_INPUT_SELECTORS: tuple[str, ...] = (
    "input[name='title']",
    "input[name='blogTitle']",
    "input#title",
)
BODY_TEXTAREA_SELECTORS: tuple[str, ...] = (
    "textarea[name='body']",
    "textarea[name='content']",
    "textarea[name='blogBody']",
    "textarea[name='blogContent']",
)
IMAGE_UPLOAD_OPEN_SELECTORS: tuple[str, ...] = (
    "input[type='button'][value='画像アップロード']",
    "button:has-text('画像アップロード')",
    "a:has-text('画像アップロード')",
)
MODAL_FILE_INPUT_SELECTORS: tuple[str, ...] = (
    "input[type='file']:visible",
    "input[type='file']",
)
MODAL_REGISTER_SELECTORS: tuple[str, ...] = (
    "button:has-text('登録する')",
    "input[type='button'][value='登録する']",
    "input[type='submit'][value='登録する']",
    "a:has-text('登録する')",
)

# 予約投稿
SCHEDULE_RADIO_NO_SELECTORS: tuple[str, ...] = (
    "label:has-text('設定しない')",
    "input[type='radio'][value='0']",
)
SCHEDULE_RADIO_YES_SELECTORS: tuple[str, ...] = (
    "label:has-text('設定する')",
    "input[type='radio'][value='1']",
    "input[type='radio'][value='reserved']",
)
SCHEDULE_DATE_SELECTORS: tuple[str, ...] = (
    "input[name='reservedDate']",
    "input[name='reserveDate']",
    "input[type='date']",
)
SCHEDULE_HOUR_SELECTORS: tuple[str, ...] = (
    "select[name='reservedHour']",
    "select[name='reserveHour']",
    "select[name='hour']",
)
SCHEDULE_MINUTE_SELECTORS: tuple[str, ...] = (
    "select[name='reservedMinute']",
    "select[name='reserveMinute']",
    "select[name='minute']",
)

CONFIRM_BUTTON_SELECTORS: tuple[str, ...] = (
    "input[type='button'][value='確認する']",
    "input[type='submit'][value='確認する']",
    "button:has-text('確認する')",
    "a:has-text('確認する')",
)

# Buttons on the preview / confirmation page
PREVIEW_DRAFT_SAVE_SELECTORS: tuple[str, ...] = (
    "button:has-text('下書き保存')",
    "input[type='button'][value='下書き保存']",
    "input[type='submit'][value='下書き保存']",
    "a:has-text('下書き保存')",
)
PREVIEW_SCHEDULE_POST_SELECTORS: tuple[str, ...] = (
    "button:has-text('予約投稿')",
    "button:has-text('予約する')",
    "input[type='button'][value='予約投稿']",
    "input[type='button'][value='予約する']",
    "input[type='submit'][value='予約投稿']",
    "input[type='submit'][value='予約する']",
    "a:has-text('予約投稿')",
    "a:has-text('予約する')",
)

# --- Result --------------------------------------------------------------- #


@dataclass
class PostResult:
    success: bool
    final_url: str | None = None
    error: str | None = None
    screenshots: list[Path] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "final_url": self.final_url,
            "error": self.error,
            "screenshots": [str(p) for p in self.screenshots],
        }


@dataclass
class BatchPostItem:
    """One unit of work for ``SalonBoardPoster.post_batch_scheduled``."""
    title: str
    body: str
    image_path: Path
    scheduled_dt: datetime
    poster: str
    category: str = "おすすめメニュー"
    # Per-item label used for naming screenshots so debug artifacts don't collide.
    label: str = ""


# --- Helpers (pure, testable) --------------------------------------------- #


def _safe_filename(label: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in label) or "step"


def http_preflight(url: str = SALON_BOARD_LOGIN_URL, timeout_s: int = 15) -> dict[str, Any]:
    """Best-effort raw HTTP probe with a Firefox TLS fingerprint.

    Uses curl_cffi (libcurl-impersonate) because Akamai on salonboard.com
    silently drops requests with Python's default ``requests`` TLS fingerprint.
    Firefox impersonation matches what our Playwright Firefox session uses.

    Failure here is non-fatal — Playwright is the source of truth for the
    actual run. The preflight is purely diagnostic.
    """
    summary: dict[str, Any] = {"url": url}
    try:
        # Lazy import so unit tests don't require curl_cffi at module load.
        from curl_cffi import requests as cffi_requests  # type: ignore[import-untyped]
        resp = cffi_requests.get(
            url,
            impersonate="firefox133",
            timeout=timeout_s,
            headers={"Accept-Language": "ja-JP,ja;q=0.9"},
            allow_redirects=True,
        )
        summary.update(
            ok=True,
            status_code=resp.status_code,
            content_length=len(resp.content),
            final_url=resp.url,
            server=resp.headers.get("server", ""),
        )
        log.info(
            "HTTP preflight (curl_cffi firefox133): status=%s len=%d final=%s server=%s",
            resp.status_code, len(resp.content), resp.url, resp.headers.get("server", ""),
        )
    except Exception as e:  # noqa: BLE001
        summary.update(ok=False, error=f"{type(e).__name__}: {e}")
        log.warning("HTTP preflight failed (non-fatal): %s", e)
    return summary


# Init script to hide navigator.webdriver. We deliberately keep this minimal
# now that we're on Firefox: Chromium-specific shims (window.chrome, plugins
# named "Chrome PDF Plugin") would themselves be suspicious on a Firefox UA.
_STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'languages', { get: () => ['ja-JP', 'ja', 'en'] });
"""


# Firefox-specific launch preferences (Playwright Firefox accepts these via
# firefox_user_prefs rather than --args).
_FIREFOX_USER_PREFS = {
    "intl.accept_languages": "ja-JP,ja",
    "general.useragent.locale": "ja-JP",
    # Disable webdriver flag at the engine level
    "dom.webdriver.enabled": False,
    # Skip safebrowsing lookups to speed up cold starts in CI
    "browser.safebrowsing.malware.enabled": False,
    "browser.safebrowsing.phishing.enabled": False,
}


# --- Poster ---------------------------------------------------------------- #


class SalonBoardPoster:
    """Drives the Salon Board UI to save a blog post as draft or scheduled."""

    def __init__(
        self,
        user_id: str,
        password: str,
        *,
        headless: bool = True,
        screenshots_dir: Path | None = None,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        per_selector_timeout_ms: int = PER_SELECTOR_TIMEOUT_MS,
    ) -> None:
        if not user_id or not password:
            raise ValueError("user_id and password are required")
        self.user_id = user_id
        self.password = password
        self.headless = headless
        self.screenshots_dir = screenshots_dir or SCREENSHOTS_DIR
        self.timeout_ms = timeout_ms
        self.per_selector_timeout_ms = per_selector_timeout_ms
        self._step = 0
        self._screenshots: list[Path] = []

    # ----- screenshot ----- #

    def _screenshot(self, page: Page, label: str) -> Path:
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        self._step += 1
        name = f"{self._step:02d}_{_safe_filename(label)}.png"
        path = self.screenshots_dir / name
        # Try full-page first, then viewport-only as fallback so a stuck page
        # doesn't bury the diagnostic with another timeout.
        for kwargs in (
            {"full_page": True, "animations": "disabled", "timeout": SCREENSHOT_TIMEOUT_MS},
            {"full_page": False, "animations": "disabled", "timeout": 5000},
        ):
            try:
                page.screenshot(path=str(path), **kwargs)
                self._screenshots.append(path)
                log.info("Screenshot: %s (%s)", path, "full" if kwargs["full_page"] else "viewport")
                return path
            except Exception as e:  # noqa: BLE001
                log.warning("Screenshot %s failed (%s): %s", label, kwargs, e)
        return path

    # ----- navigation with progressive fallback ----- #

    def _navigate(
        self,
        page: Page,
        url: str,
        label: str,
        *,
        wait_for_selectors: Sequence[str] | None = None,
    ) -> None:
        """Navigate with: domcontentloaded → commit → wait_for_selector fallback."""
        # Attempt 1: full DOMContentLoaded
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS)
            log.info("Navigated to %s (%s) via domcontentloaded", url, label)
            return
        except PWTimeout:
            log.warning(
                "domcontentloaded timed out for %s after %dms; retrying with commit",
                label, NAVIGATION_TIMEOUT_MS,
            )

        # Attempt 2: commit-only (just URL change). Some pages stall on a hung
        # subresource; this gets us into the page so we can poll for selectors.
        try:
            page.goto(url, wait_until="commit", timeout=NAVIGATION_TIMEOUT_MS)
            log.info("Navigated to %s (%s) via commit", url, label)
        except PWTimeout as e:
            log.error("commit navigation also failed for %s: %s", label, e)
            raise

        # Attempt 3: explicitly wait for an expected selector if provided
        if wait_for_selectors:
            for sel in wait_for_selectors:
                try:
                    page.locator(sel).first.wait_for(
                        state="visible", timeout=NAVIGATION_TIMEOUT_MS,
                    )
                    log.info("Selector %s appeared after commit-only navigation", sel)
                    return
                except PWTimeout:
                    log.debug("wait_for %s timed out; trying next", sel)
            log.warning("None of %d expected selectors appeared after commit-only nav", len(wait_for_selectors))

    # ----- selector tries (auto-wait via Playwright timeout) ----- #

    def _try_fill(
        self,
        page: Page,
        selectors: Sequence[str],
        value: str,
        label: str,
    ) -> bool:
        for sel in selectors:
            try:
                page.locator(sel).first.fill(value, timeout=self.per_selector_timeout_ms)
                log.info("Filled %s via selector: %s", label, sel)
                return True
            except Exception as e:  # noqa: BLE001
                log.debug("Fill %s with %s failed: %s", label, sel, e)
        log.warning("Could not fill %s with any of %d selectors", label, len(selectors))
        return False

    def _try_click(self, page: Page, selectors: Sequence[str], label: str) -> bool:
        for sel in selectors:
            try:
                page.locator(sel).first.click(timeout=self.per_selector_timeout_ms)
                log.info("Clicked %s via selector: %s", label, sel)
                return True
            except Exception as e:  # noqa: BLE001
                log.debug("Click %s with %s failed: %s", label, sel, e)
        log.warning("Could not click %s with any of %d selectors", label, len(selectors))
        return False

    def _safe_eval(self, page: Page, expression: str) -> Any:
        """Evaluate JS, swallowing exceptions (returns None on failure)."""
        try:
            return page.evaluate(expression)
        except Exception as e:  # noqa: BLE001
            log.debug("Safe eval failed for %r: %s", expression, e)
            return None

    def _log_akamai_cookies(self, page: Page, label: str) -> None:
        """Log presence and length of Akamai Bot Manager cookies.

        Akamai's _abck cookie value encodes a bot-score state. Its presence and
        a healthy structure (~~~0 suffix means "not yet validated") is required
        for POST endpoints to accept the request. Logging this helps diagnose
        whether a POST drop is "missing _abck" vs "fingerprint rejected".
        """
        try:
            cookies = page.context.cookies()
        except Exception as e:  # noqa: BLE001
            log.debug("cookies() failed: %s", e)
            return
        names_of_interest = {"_abck", "ak_bmsc", "bm_sz", "bm_sv", "bm_mi"}
        summary = []
        for c in cookies:
            if c.get("name") in names_of_interest:
                val = c.get("value", "")
                suffix = val[-4:] if val else ""
                summary.append(f"{c['name']}(len={len(val)}, tail={suffix!r})")
        log.info("Akamai cookies @ %s: %s",
                 label, ", ".join(summary) if summary else "(none)")

    @staticmethod
    def _abck_is_validated(cookie_value: str) -> bool:
        """Heuristic: _abck is considered validated when its last segment != '-1'.

        Akamai's _abck cookie is tilde-delimited; the LAST segment is the
        bot-score state. '-1' means "challenge pending / not yet validated";
        anything else (typically '0', a session token, or a count) indicates
        the sensor JS has accepted the client. POST endpoints are usually
        rejected until this transitions away from '-1'.
        """
        parts = cookie_value.split("~")
        return len(parts) >= 2 and parts[-1] != "-1"

    def _wait_for_akamai_validation(self, page: Page, max_wait_s: int = 20) -> bool:
        """Poll until _abck transitions away from '~-1' tail (validated)."""
        import time as _t
        deadline = _t.monotonic() + max_wait_s
        last_tail = ""
        while _t.monotonic() < deadline:
            try:
                cookies = page.context.cookies()
            except Exception:  # noqa: BLE001
                cookies = []
            for c in cookies:
                if c.get("name") == "_abck":
                    val = c.get("value", "")
                    tail = val.split("~")[-1] if val else ""
                    if tail != last_tail:
                        log.info("_abck tail update: %r", tail)
                        last_tail = tail
                    if self._abck_is_validated(val):
                        return True
            try:
                page.wait_for_timeout(500)
            except Exception:  # noqa: BLE001
                break
        return False

    def _simulate_human_interaction(self, page: Page) -> None:
        """Generate realistic mouse / scroll / focus events.

        Akamai's bot manager scores the client based on observed behavioural
        signals (mousemove, scroll, keystroke timing). Headless automation
        with synthetic events typically scores as bot; without a higher score,
        the _abck cookie never validates and POST endpoints reject the request.
        This routine fires a small but realistic burst of events.
        """
        try:
            # Move mouse to several positions, with multi-step interpolation
            # (Playwright fires multiple mousemove events per step).
            positions = [(120, 180), (480, 320), (640, 240), (260, 480), (520, 360)]
            for x, y in positions:
                page.mouse.move(x, y, steps=15)
                page.wait_for_timeout(140)
            # A small scroll down then up
            page.mouse.wheel(0, 80)
            page.wait_for_timeout(220)
            page.mouse.wheel(0, -60)
            page.wait_for_timeout(220)
            # Hover the body to trigger more activity
            try:
                page.locator("body").first.hover(timeout=1500)
            except Exception:  # noqa: BLE001
                pass
        except Exception as e:  # noqa: BLE001
            log.debug("Human-interaction simulation failed: %s", e)

    def _type_into(
        self, page: Page, selectors: Sequence[str], value: str, label: str,
        delay_ms: int = 60,
    ) -> bool:
        """Click+type into the first matching input, firing real key events.

        Replaces fill() for fields where Akamai-style sensors need to observe
        keydown/keypress/keyup. .fill() merely sets the DOM value property,
        which fires only 'input' but NO keyboard events.
        """
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                # Real mouse click to focus (also fires Akamai-visible events)
                loc.click(timeout=self.per_selector_timeout_ms)
                page.wait_for_timeout(80)
                # Clear any existing value, then type with realistic per-key delay
                try:
                    loc.fill("", timeout=1000)
                except Exception:  # noqa: BLE001
                    pass
                loc.type(value, delay=delay_ms)
                log.info("Typed %s via selector: %s", label, sel)
                return True
            except Exception as e:  # noqa: BLE001
                log.debug("Type %s with %s failed: %s", label, sel, e)
        log.warning("Could not type %s via any of %d selectors", label, len(selectors))
        return False

    def _wait_for_login_complete(self, page: Page, timeout_s: int = 45) -> bool:
        """Wait until the page navigates away from the login URL.

        Polls every 500ms, also detecting Firefox's neterror state as a fail.
        Returns True if URL no longer contains '/login', False otherwise.
        """
        import time as _t
        deadline = _t.monotonic() + timeout_s
        login_marker = "/login"
        last_logged = ""
        while _t.monotonic() < deadline:
            current = page.url
            if login_marker not in current:
                return True
            body_cls = self._safe_eval(page, "document.body && document.body.className || ''") or ""
            if "neterror" in body_cls:
                log.warning("Firefox neterror detected during login wait")
                return False
            if current != last_logged:
                log.info("Login wait: still at %s", current)
                last_logged = current
            try:
                page.wait_for_timeout(500)
            except Exception:  # noqa: BLE001
                break
        return False

    def _dump_form_html(self, page: Page, label: str) -> None:
        """Save the relevant form's outerHTML so selector mismatches can be diagnosed.

        Persisted to ``screenshots_dir/<label>_form.html`` and the first 1500
        characters are logged inline so artifact download isn't required.
        """
        try:
            # Get the form that contains the password input (most reliable anchor).
            html = page.evaluate(
                """() => {
                    const pw = document.querySelector("input[type='password'], input[name='password']");
                    const form = pw && pw.closest('form');
                    return form ? form.outerHTML : document.body.outerHTML.slice(0, 8000);
                }"""
            )
        except Exception as e:  # noqa: BLE001
            log.warning("Failed to dump form HTML: %s", e)
            return
        try:
            self.screenshots_dir.mkdir(parents=True, exist_ok=True)
            path = self.screenshots_dir / f"{label}_form.html"
            path.write_text(html or "", encoding="utf-8")
            log.warning("Dumped %s form HTML to %s (%d chars)", label, path, len(html or ""))
        except Exception as e:  # noqa: BLE001
            log.warning("Failed to write form HTML: %s", e)
        # Also log a truncated preview so we don't have to wait for artifact download
        preview = (html or "")[:1500].replace("\n", " ")
        log.warning("=== %s_form HTML preview (first 1500 chars) ===", label)
        log.warning(preview)
        log.warning("=== end %s_form HTML preview ===", label)

    def _try_js_submit_login(self, page: Page) -> bool:
        """Last-resort: programmatically submit the login form via JavaScript.

        Many Salon Board / Akamai-protected sites bind login to an <a> onclick
        handler rather than a submit button. If our selector list can't find the
        right element, we instead call form.submit() directly. Returns True if
        navigation away from the login page occurred.
        """
        login_url_prefix = SALON_BOARD_LOGIN_URL.rstrip("/")
        try:
            result = page.evaluate(
                """() => {
                    const pw = document.querySelector("input[type='password'], input[name='password']");
                    const form = pw && pw.closest('form');
                    if (!form) return {ok: false, reason: 'no-form'};
                    try { form.submit(); return {ok: true}; }
                    catch (e) { return {ok: false, reason: String(e)}; }
                }"""
            )
        except Exception as e:  # noqa: BLE001
            log.warning("JS form.submit() raised: %s", e)
            return False
        if not result or not result.get("ok"):
            log.warning("JS form.submit() did not run: %s", result)
            return False
        # Wait briefly for navigation. We don't strictly need wait_for_url here —
        # success is indicated by URL leaving the login path.
        try:
            page.wait_for_load_state("domcontentloaded", timeout=self.timeout_ms)
        except Exception:  # noqa: BLE001
            pass
        current = page.url.split("?")[0].split("#")[0].rstrip("/")
        return current != login_url_prefix

    def _try_select(
        self,
        page: Page,
        selectors: Sequence[str],
        label_text: str,
        debug_label: str,
    ) -> bool:
        """Select a dropdown option by its visible label text."""
        for sel in selectors:
            try:
                page.locator(sel).first.select_option(
                    label=label_text, timeout=self.per_selector_timeout_ms,
                )
                log.info("Selected %s=%s via selector: %s", debug_label, label_text, sel)
                return True
            except Exception as e:  # noqa: BLE001
                log.debug("Select %s=%s with %s failed: %s", debug_label, label_text, sel, e)
        log.warning(
            "Could not select %s=%s with any of %d selectors",
            debug_label, label_text, len(selectors),
        )
        return False

    def _try_set_files(
        self,
        page: Page,
        selectors: Sequence[str],
        file_path: Path,
        label: str,
    ) -> bool:
        for sel in selectors:
            try:
                page.locator(sel).first.set_input_files(
                    str(file_path), timeout=self.per_selector_timeout_ms,
                )
                log.info("Set %s file via selector: %s", label, sel)
                return True
            except Exception as e:  # noqa: BLE001
                log.debug("Set files %s with %s failed: %s", label, sel, e)
        log.warning("Could not set %s file with any selector", label)
        return False

    def _wait_idle(self, page: Page) -> None:
        try:
            page.wait_for_load_state("networkidle", timeout=self.timeout_ms)
        except PWTimeout:
            # Surface this clearly — silent swallowing previously hid the symptom
            # that the page was stuck on a never-completing subresource.
            log.warning(
                "networkidle wait of %dms timed out on %s; proceeding anyway "
                "(some background resource may still be loading)",
                self.timeout_ms, page.url,
            )

    # ----- public entry ----- #

    def post_as_draft(
        self,
        title: str,
        body: str,
        image_path: Path,
        *,
        poster: str | None = None,
        category: str = DEFAULT_CATEGORY,
    ) -> PostResult:
        return self._post(
            title, body, image_path, scheduled_dt=None,
            poster=poster, category=category,
        )

    def post_as_scheduled(
        self,
        title: str,
        body: str,
        image_path: Path,
        scheduled_dt: datetime,
        *,
        poster: str | None = None,
        category: str = DEFAULT_CATEGORY,
    ) -> PostResult:
        return self._post(
            title, body, image_path, scheduled_dt=scheduled_dt,
            poster=poster, category=category,
        )

    def post_batch_scheduled(
        self,
        items: list[BatchPostItem],
        *,
        between_items_pause_sec: float = 5.0,
    ) -> list[PostResult]:
        """Login once, then post each scheduled item in sequence.

        Used for the weekly-batch workflow where the user runs the script on
        their PC and 7 days of posts are scheduled in one browser session.

        Failure isolation: if item N fails we continue with item N+1 — the
        succeeded items remain valid scheduled posts in Salon Board.
        """
        if not items:
            return []
        for it in items:
            if not it.title:
                raise ValueError("title is required for every batch item")
            if not it.body:
                raise ValueError("body is required for every batch item")
            if not Path(it.image_path).exists():
                raise FileNotFoundError(f"image_path missing: {it.image_path}")
            if not it.poster:
                raise ValueError("poster is required for every batch item")

        log.info("Batch posting %d scheduled items", len(items))
        http_preflight()

        results: list[PostResult] = []
        with sync_playwright() as p:
            # Firefox (NOT Chromium): Akamai on salonboard.com silently drops
            # HTTP responses with a Chromium TLS fingerprint. Firefox passes.
            browser = p.firefox.launch(
                headless=self.headless,
                firefox_user_prefs=_FIREFOX_USER_PREFS,
            )
            try:
                context = browser.new_context(
                    locale="ja-JP",
                    timezone_id="Asia/Tokyo",
                    viewport={"width": 1280, "height": 800},
                    user_agent=DEFAULT_USER_AGENT,
                    extra_http_headers={"Accept-Language": "ja-JP,ja;q=0.9,en;q=0.5"},
                )
                context.add_init_script(_STEALTH_INIT_SCRIPT)
                page = context.new_page()
                page.set_default_timeout(self.timeout_ms)
                page.set_default_navigation_timeout(NAVIGATION_TIMEOUT_MS)

                # 1. Single login
                self._login(page)

                # 2. Post each item
                for idx, item in enumerate(items, start=1):
                    item_label = item.label or item.scheduled_dt.strftime("%Y%m%d")
                    log.info(
                        "=== Batch item %d/%d (label=%s, publish=%s) ===",
                        idx, len(items), item_label, item.scheduled_dt.isoformat(),
                    )
                    # Reset per-item screenshot state so each item has its own series.
                    self._step = 0
                    prior_screenshots = list(self._screenshots)
                    self._screenshots = []
                    # Stash per-item screenshots under day-prefixed subdir
                    original_dir = self.screenshots_dir
                    self.screenshots_dir = original_dir / f"day{idx:02d}_{item_label}"

                    try:
                        self._navigate_to_blog_new(page)
                        self._fill_form(
                            page,
                            title=item.title,
                            body=item.body,
                            image_path=Path(item.image_path),
                            poster=item.poster,
                            category=item.category,
                            scheduled_dt=item.scheduled_dt,
                        )
                        final_url = self._submit_and_finalize(page, item.scheduled_dt)
                        results.append(PostResult(
                            success=True,
                            final_url=final_url,
                            screenshots=list(self._screenshots),
                        ))
                        log.info("Batch item %d/%d posted: %s", idx, len(items), final_url)
                    except Exception as e:  # noqa: BLE001
                        log.exception("Batch item %d/%d failed: %s", idx, len(items), e)
                        try:
                            self._screenshot(page, "error_state")
                        except Exception:  # noqa: BLE001
                            pass
                        results.append(PostResult(
                            success=False,
                            error=f"{type(e).__name__}: {e}",
                            screenshots=list(self._screenshots),
                        ))
                    finally:
                        # Restore parent screenshots dir and accumulate
                        prior_screenshots.extend(self._screenshots)
                        self._screenshots = prior_screenshots
                        self.screenshots_dir = original_dir

                    # Throttle between items to avoid tripping any rate limit
                    if idx < len(items):
                        try:
                            page.wait_for_timeout(int(between_items_pause_sec * 1000))
                        except Exception:  # noqa: BLE001
                            pass

                return results
            finally:
                browser.close()

    def _post(
        self,
        title: str,
        body: str,
        image_path: Path,
        scheduled_dt: datetime | None,
        poster: str | None,
        category: str,
    ) -> PostResult:
        if not title:
            raise ValueError("title is required")
        if not body:
            raise ValueError("body is required")
        image_path = Path(image_path)
        if not image_path.exists():
            raise FileNotFoundError(f"image_path does not exist: {image_path}")

        if poster is None:
            poster = get_poster_for_date(date.today())

        log.info(
            "Posting: poster=%s, category=%s, scheduled=%s, title=%r",
            poster, category,
            scheduled_dt.isoformat() if scheduled_dt else "(draft)",
            title,
        )

        # HTTP preflight: log connectivity / status from the runner's IP.
        # If this fails or returns non-200, the issue is upstream of Playwright.
        http_preflight()

        with sync_playwright() as p:
            # Firefox (NOT Chromium): see note at the post_batch_scheduled launch.
            browser = p.firefox.launch(
                headless=self.headless,
                firefox_user_prefs=_FIREFOX_USER_PREFS,
            )
            page: Page | None = None
            try:
                context = browser.new_context(
                    locale="ja-JP",
                    timezone_id="Asia/Tokyo",
                    viewport={"width": 1280, "height": 800},
                    user_agent=DEFAULT_USER_AGENT,
                    extra_http_headers={"Accept-Language": "ja-JP,ja;q=0.9,en;q=0.5"},
                )
                # Hide common automation fingerprints
                context.add_init_script(_STEALTH_INIT_SCRIPT)
                page = context.new_page()
                page.set_default_timeout(self.timeout_ms)
                page.set_default_navigation_timeout(NAVIGATION_TIMEOUT_MS)

                self._login(page)
                self._navigate_to_blog_new(page)
                self._fill_form(
                    page,
                    title=title,
                    body=body,
                    image_path=image_path,
                    poster=poster,
                    category=category,
                    scheduled_dt=scheduled_dt,
                )
                final_url = self._submit_and_finalize(page, scheduled_dt)

                return PostResult(
                    success=True,
                    final_url=final_url,
                    screenshots=list(self._screenshots),
                )
            except Exception as e:  # noqa: BLE001
                log.exception("Salon Board posting failed")
                if page is not None:
                    self._screenshot(page, "error_state")
                return PostResult(
                    success=False,
                    error=f"{type(e).__name__}: {e}",
                    screenshots=list(self._screenshots),
                )
            finally:
                browser.close()

    # ----- steps ----- #

    def _login(self, page: Page) -> None:
        log.info("Loading login: %s", SALON_BOARD_LOGIN_URL)
        self._navigate(
            page, SALON_BOARD_LOGIN_URL, "login_page",
            wait_for_selectors=LOGIN_ID_SELECTORS,
        )
        self._screenshot(page, "01_login_page")
        # Initial form HTML for diagnosability (no input values in outerHTML).
        self._dump_form_html(page, "01_login_initial")

        # Phase 1: let Akamai's sensor JS load and register the page.
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:  # noqa: BLE001
            pass
        page.wait_for_timeout(1000)
        self._log_akamai_cookies(page, "after_load")

        # Phase 2: human-like interaction to feed Akamai's behavioural sensor.
        # Without this, _abck stays at '~-1' (challenge pending) and the POST
        # endpoint silently drops the request. We then poll _abck until it
        # transitions away from '-1' — or up to 20s, then proceed regardless.
        log.info("Simulating human interaction to advance Akamai sensor...")
        self._simulate_human_interaction(page)
        if self._wait_for_akamai_validation(page, max_wait_s=20):
            log.info("_abck validated — Akamai sensor accepted the client")
        else:
            log.warning("_abck not validated within 20s — POST may still be dropped")
        self._log_akamai_cookies(page, "after_human_sim")

        # Phase 3: type credentials with realistic per-key delay so the sensor
        # observes keydown/keypress/keyup (fill() does NOT fire these).
        if not self._type_into(page, LOGIN_ID_SELECTORS, self.user_id, "login_id"):
            # Fallback to .fill() if .type() couldn't find inputs.
            if not self._try_fill(page, LOGIN_ID_SELECTORS, self.user_id, "login_id"):
                raise RuntimeError("Could not locate login ID input")
        if not self._type_into(page, LOGIN_PW_SELECTORS, self.password, "login_pw"):
            if not self._try_fill(page, LOGIN_PW_SELECTORS, self.password, "login_pw"):
                raise RuntimeError("Could not locate password input")
        # NOTE: no "02_login_filled" screenshot — would leak loginID in artifacts.

        # Give Akamai 1-2 more seconds to process the keypress burst.
        page.wait_for_timeout(1500)

        # Detect early if a background request put the page in Firefox's neterror.
        body_class = self._safe_eval(page, "document.body && document.body.className || ''")
        if body_class and "neterror" in body_class:
            raise RuntimeError(
                f"Page became Firefox neterror after fill (body.class={body_class!r}); "
                "Akamai likely blocked a background request from the login page."
            )
        self._log_akamai_cookies(page, "after_typing")

        # ----- Submit (three strategies, each with no_wait_after so we don't
        #       hang waiting for navigation that Akamai might silently drop). -----
        submitted_via: str | None = None

        # Strategy 1: click the verified login button selector.
        for sel in LOGIN_SUBMIT_SELECTORS:
            try:
                page.locator(sel).first.click(
                    timeout=5000,
                    # CRITICAL: don't wait for navigation after click. Akamai
                    # may silently drop the POST; without this flag, click()
                    # hangs for `timeout` ms even though the click landed.
                    no_wait_after=True,
                )
                submitted_via = f"click({sel})"
                log.info("login_submit: clicked %s", sel)
                break
            except Exception as e:  # noqa: BLE001
                log.debug("login_submit click %s failed: %s", sel, e)

        # Strategy 2: press Enter on the password field. The page has
        # onkeypress="enterActionLogin(event)" on both inputs, so Enter
        # triggers the same submit path as the button.
        if submitted_via is None:
            try:
                page.locator("input[name='password']").first.press(
                    "Enter", timeout=3000, no_wait_after=True,
                )
                submitted_via = "keypress(Enter on password)"
                log.info("login_submit: triggered via Enter keypress")
            except Exception as e:  # noqa: BLE001
                log.debug("login_submit Enter-keypress failed: %s", e)

        # Strategy 3: call dologin() directly via JS.
        if submitted_via is None:
            result = self._safe_eval(page, """() => {
                if (typeof dologin !== 'function') return {ok: false, reason: 'dologin undefined'};
                try { dologin(new Event('click')); return {ok: true}; }
                catch (e) { return {ok: false, reason: String(e)}; }
            }""")
            if result and result.get("ok"):
                submitted_via = "js(dologin)"
                log.info("login_submit: triggered via JS dologin()")
            else:
                log.warning("login_submit JS dologin failed: %s", result)

        if submitted_via is None:
            self._dump_form_html(page, "02_no_submit_method_worked")
            raise RuntimeError(
                "All login submit strategies failed (click / Enter keypress / JS dologin)"
            )

        # ----- Wait for login completion or hard-fail -----
        if not self._wait_for_login_complete(page, timeout_s=45):
            self._dump_form_html(page, "03_login_did_not_complete")
            self._log_akamai_cookies(page, "after_failed_submit")
            body_cls = self._safe_eval(page, "document.body && document.body.className || ''") or ""
            url = page.url
            if "neterror" in body_cls:
                raise RuntimeError(
                    f"Login POST was silently dropped by Akamai "
                    f"(Firefox neterror after submit via {submitted_via}; url={url}). "
                    "TLS fingerprint passes for GET but POST is blocked. "
                    "Next step: switch login to curl_cffi HTTP-only flow."
                )
            raise RuntimeError(
                f"Login did not complete within 45s (submitted via {submitted_via}, "
                f"still at url={url}, body.class={body_cls!r})"
            )
        log.info("Login complete via %s; url=%s", submitted_via, page.url)
        self._wait_idle(page)
        self._screenshot(page, "03_after_login")

        # Precise login-success check: we must have NAVIGATED AWAY from the
        # original login URL. A substring match on "login" would false-positive
        # on legitimate post-login paths like "/post-login/" or "/?from=login".
        current_url = page.url.split("?")[0].split("#")[0].rstrip("/")
        login_url = SALON_BOARD_LOGIN_URL.split("?")[0].split("#")[0].rstrip("/")
        if current_url == login_url:
            raise RuntimeError(f"Login likely failed; still on {page.url}")

    def _navigate_to_blog_new(self, page: Page) -> None:
        log.info("Going to blog new form: %s", SALON_BOARD_BLOG_NEW_URL)
        self._navigate(
            page, SALON_BOARD_BLOG_NEW_URL, "blog_new_form",
            wait_for_selectors=TITLE_INPUT_SELECTORS,
        )
        self._wait_idle(page)
        self._screenshot(page, "04_blog_new_form")

    def _fill_form(
        self,
        page: Page,
        *,
        title: str,
        body: str,
        image_path: Path,
        poster: str,
        category: str,
        scheduled_dt: datetime | None,
    ) -> None:
        log.info("Filling form: title_len=%d body_len=%d", len(title), len(body))

        # 投稿者
        if not self._try_select(page, POSTER_SELECT_SELECTORS, poster, "poster"):
            log.warning("Failed to select poster=%s; continuing", poster)
        self._screenshot(page, "05_poster_selected")

        # カテゴリ
        if not self._try_select(page, CATEGORY_SELECT_SELECTORS, category, "category"):
            log.warning("Failed to select category=%s; continuing", category)
        self._screenshot(page, "06_category_selected")

        # タイトル
        if not self._try_fill(page, TITLE_INPUT_SELECTORS, title, "title"):
            raise RuntimeError("Could not locate title input")

        # 本文
        if not self._try_fill(page, BODY_TEXTAREA_SELECTORS, body, "body"):
            raise RuntimeError("Could not locate body textarea")
        self._screenshot(page, "07_text_filled")

        # 画像アップロード (modal)
        self._upload_image(page, image_path)
        self._screenshot(page, "09_after_image_upload")

        # 予約投稿
        if scheduled_dt is not None:
            self._set_scheduled_datetime(page, scheduled_dt)
            self._screenshot(page, "10_schedule_set")

    def _upload_image(self, page: Page, image_path: Path) -> None:
        log.info("Uploading image: %s", image_path)
        # Open the modal
        if not self._try_click(page, IMAGE_UPLOAD_OPEN_SELECTORS, "image_upload_open"):
            log.warning("Could not find 画像アップロード button; trying direct file input")
        # Give the modal a moment to render
        try:
            page.wait_for_timeout(800)
        except Exception:  # noqa: BLE001
            pass
        self._screenshot(page, "08_image_upload_modal")

        if not self._try_set_files(page, MODAL_FILE_INPUT_SELECTORS, image_path, "image_file"):
            log.warning("Could not set image file via any input")
            return
        # Wait briefly for any client-side processing / preview
        try:
            page.wait_for_timeout(2500)
        except Exception:  # noqa: BLE001
            pass

        # Click 登録する on the modal
        if not self._try_click(page, MODAL_REGISTER_SELECTORS, "modal_register"):
            log.warning("Could not click 登録する on image modal")
        try:
            page.wait_for_timeout(1500)
        except Exception:  # noqa: BLE001
            pass

    def _set_scheduled_datetime(self, page: Page, scheduled_dt: datetime) -> None:
        log.info("Setting scheduled datetime: %s", scheduled_dt.isoformat())
        # Click 設定する radio (or its label)
        if not self._try_click(page, SCHEDULE_RADIO_YES_SELECTORS, "schedule_radio_yes"):
            log.warning("Could not click 設定する radio")

        # Try multiple date formats — Salon Board may use /, -, or yyyy/m/d
        date_str_candidates = (
            scheduled_dt.strftime("%Y/%m/%d"),
            scheduled_dt.strftime("%Y-%m-%d"),
            scheduled_dt.strftime("%Y%m%d"),
        )
        date_filled = False
        for cand in date_str_candidates:
            if self._try_fill(page, SCHEDULE_DATE_SELECTORS, cand, f"schedule_date({cand})"):
                date_filled = True
                break
        if not date_filled:
            log.warning("Could not fill scheduled date")

        # Hour / minute dropdowns (often 0-padded "08", "15")
        hour_str = f"{scheduled_dt.hour:02d}"
        minute_str = f"{scheduled_dt.minute:02d}"
        if not self._try_select(page, SCHEDULE_HOUR_SELECTORS, hour_str, "schedule_hour"):
            log.warning("Could not select scheduled hour")
        if not self._try_select(page, SCHEDULE_MINUTE_SELECTORS, minute_str, "schedule_minute"):
            log.warning("Could not select scheduled minute")

    def _submit_and_finalize(
        self,
        page: Page,
        scheduled_dt: datetime | None,
    ) -> str | None:
        log.info("Clicking 確認する")
        if not self._try_click(page, CONFIRM_BUTTON_SELECTORS, "confirm_button"):
            raise RuntimeError("Could not locate 確認する button on the form")
        self._wait_idle(page)
        self._screenshot(page, "11_preview_page")

        # On the preview page, click the final action button
        if scheduled_dt is None:
            log.info("Clicking 下書き保存 on preview")
            if not self._try_click(page, PREVIEW_DRAFT_SAVE_SELECTORS, "preview_draft_save"):
                raise RuntimeError("Could not locate 下書き保存 on preview page")
        else:
            log.info("Clicking 予約投稿 on preview")
            if not self._try_click(page, PREVIEW_SCHEDULE_POST_SELECTORS, "preview_schedule_post"):
                raise RuntimeError("Could not locate 予約投稿/予約する on preview page")
        self._wait_idle(page)
        self._screenshot(page, "12_after_finalize")
        return page.url


# --- Convenience function -------------------------------------------------- #


def _require_credentials() -> tuple[str, str]:
    user_id = os.environ.get("SALON_BOARD_ID")
    password = os.environ.get("SALON_BOARD_PASSWORD")
    if not user_id or not password:
        raise RuntimeError(
            "SALON_BOARD_ID and SALON_BOARD_PASSWORD environment variables are required",
        )
    return user_id, password


def post_blog_as_draft(
    title: str,
    body: str,
    image_path: Path,
    *,
    poster: str | None = None,
    category: str = DEFAULT_CATEGORY,
    headless: bool = True,
) -> PostResult:
    user_id, password = _require_credentials()
    poster_obj = SalonBoardPoster(user_id, password, headless=headless)
    return poster_obj.post_as_draft(
        title, body, image_path, poster=poster, category=category,
    )


def post_blog_scheduled(
    title: str,
    body: str,
    image_path: Path,
    scheduled_dt: datetime,
    *,
    poster: str | None = None,
    category: str = DEFAULT_CATEGORY,
    headless: bool = True,
) -> PostResult:
    user_id, password = _require_credentials()
    poster_obj = SalonBoardPoster(user_id, password, headless=headless)
    return poster_obj.post_as_scheduled(
        title, body, image_path, scheduled_dt,
        poster=poster, category=category,
    )
