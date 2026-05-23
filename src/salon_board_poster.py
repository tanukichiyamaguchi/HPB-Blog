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

# Modern Chrome on macOS user-agent — Salon Board is browser-only and may
# refuse the default Playwright UA.
DEFAULT_USER_AGENT = os.environ.get(
    "SB_USER_AGENT",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
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
    "a.common-CNCcommon__primaryBtn:has-text('ログイン')",
    "a:text-is('ログイン')",
    "button:text-is('ログイン')",
    "input[type='submit'][value='ログイン']",
    "button:has-text('ログイン'):not(:has-text('お困り')):not(:has-text('できない'))",
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

# Optional: dismiss the "ログインでお困りですか？" helper panel if it blocks anything.
LOGIN_HELP_DISMISS_SELECTORS: tuple[str, ...] = (
    "button[aria-label='閉じる']",
    ".help-popup button.close",
    "div.help-popup .close",
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


# --- Helpers (pure, testable) --------------------------------------------- #


def _safe_filename(label: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in label) or "step"


def http_preflight(url: str = SALON_BOARD_LOGIN_URL, timeout_s: int = 15) -> dict[str, Any]:
    """Best-effort raw HTTP probe.

    Useful to differentiate geographic / network blocks (request never returns or
    returns non-200) from browser-only bot detection (request returns 200 but
    Playwright can't load page).
    """
    summary: dict[str, Any] = {"url": url}
    try:
        resp = requests.get(
            url,
            timeout=timeout_s,
            headers={"User-Agent": DEFAULT_USER_AGENT, "Accept-Language": "ja-JP,ja;q=0.9"},
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
            "HTTP preflight: status=%s len=%d final=%s server=%s",
            resp.status_code, len(resp.content), resp.url, resp.headers.get("server", ""),
        )
    except Exception as e:  # noqa: BLE001
        summary.update(ok=False, error=f"{type(e).__name__}: {e}")
        log.warning("HTTP preflight failed: %s", e)
    return summary


# Init script to soften the most obvious headless/Playwright fingerprints.
_STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'languages', { get: () => ['ja-JP', 'ja', 'en'] });
Object.defineProperty(navigator, 'plugins', {
  get: () => [{ name: 'Chrome PDF Plugin' }, { name: 'Chrome PDF Viewer' }, { name: 'Native Client' }],
});
window.chrome = window.chrome || { runtime: {} };
const originalQuery = navigator.permissions && navigator.permissions.query;
if (originalQuery) {
  navigator.permissions.query = (parameters) => (
    parameters.name === 'notifications'
      ? Promise.resolve({ state: Notification.permission })
      : originalQuery(parameters)
  );
}
"""


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
            log.warning("networkidle wait timed out; continuing")

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
            browser = p.chromium.launch(
                headless=self.headless,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    # Reduce headless/automation detection
                    "--disable-blink-features=AutomationControlled",
                    "--disable-features=IsolateOrigins,site-per-process",
                    "--lang=ja-JP",
                ],
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

        if not self._try_fill(page, LOGIN_ID_SELECTORS, self.user_id, "login_id"):
            raise RuntimeError("Could not locate login ID input")
        if not self._try_fill(page, LOGIN_PW_SELECTORS, self.password, "login_pw"):
            raise RuntimeError("Could not locate password input")
        self._screenshot(page, "02_login_filled")

        if not self._try_click(page, LOGIN_SUBMIT_SELECTORS, "login_submit"):
            raise RuntimeError("Could not locate login submit button")
        self._wait_idle(page)
        self._screenshot(page, "03_after_login")

        if "login" in page.url.lower():
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
