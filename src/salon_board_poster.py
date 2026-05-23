"""Salon Board automation via Playwright.

Phase 3 scope: login → blog new → fill (title/body/image) → save AS DRAFT.
Scheduled posting is intentionally deferred to Phase 5.

Selectors are best-effort with multiple fallbacks. Each step writes a screenshot
to ``screenshots/`` so failures can be diagnosed and selectors refined iteratively.
URLs and selector sets can be overridden via environment variables for fast
turn-around between GitHub Actions runs.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

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
SALON_BOARD_BLOG_LIST_URL = _env_url(
    "SALON_BOARD_BLOG_LIST_URL",
    "https://salonboard.com/KLP/blog/list/",
)

DEFAULT_TIMEOUT_MS = int(os.environ.get("SB_TIMEOUT_MS", "30000"))
PER_SELECTOR_TIMEOUT_MS = int(os.environ.get("SB_PER_SELECTOR_TIMEOUT_MS", "2500"))


# --- Selector candidate sets (order = priority) ----------------------------- #

LOGIN_ID_SELECTORS: tuple[str, ...] = (
    "#userId",
    "input[name='userId']",
    "input[name='loginId']",
    "input[name='id']",
    "input[type='text']:visible",
)
LOGIN_PW_SELECTORS: tuple[str, ...] = (
    "#password",
    "input[name='password']",
    "input[type='password']:visible",
)
LOGIN_SUBMIT_SELECTORS: tuple[str, ...] = (
    "button[type='submit']",
    "input[type='submit']",
    "button:has-text('ログイン')",
    "input[value='ログイン']",
    "a:has-text('ログイン')",
)
BLOG_NEW_BUTTON_SELECTORS: tuple[str, ...] = (
    "a:has-text('新規投稿')",
    "a:has-text('新規作成')",
    "button:has-text('新規投稿')",
    "a:has-text('ブログ投稿')",
    "a:has-text('新規ブログ')",
)
TITLE_INPUT_SELECTORS: tuple[str, ...] = (
    "input[name='title']",
    "#title",
    "input[placeholder*='タイトル']",
)
BODY_TEXTAREA_SELECTORS: tuple[str, ...] = (
    "textarea[name='content']",
    "textarea[name='body']",
    "textarea[name='blogBody']",
    "#body",
    "#content",
    "textarea:visible",
)
IMAGE_INPUT_SELECTORS: tuple[str, ...] = (
    "input[type='file'][name*='image']",
    "input[type='file'][name*='picture']",
    "input[type='file']",
)
DRAFT_SAVE_SELECTORS: tuple[str, ...] = (
    "button:has-text('下書き保存')",
    "input[value='下書き保存']",
    "a:has-text('下書き保存')",
    "button:has-text('下書き')",
    "input[value*='下書き']",
)
CONFIRM_DIALOG_SELECTORS: tuple[str, ...] = (
    "button:has-text('はい')",
    "button:has-text('OK')",
    "button:has-text('確定')",
    "button:has-text('保存')",
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


# --- Poster ---------------------------------------------------------------- #


class SalonBoardPoster:
    """Drives the Salon Board UI to save a blog post as draft."""

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
        try:
            page.screenshot(path=str(path), full_page=True)
            self._screenshots.append(path)
            log.info("Screenshot: %s", path)
        except Exception as e:  # noqa: BLE001
            log.warning("Screenshot failed for %s: %s", label, e)
        return path

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

    def post_as_draft(self, title: str, body: str, image_path: Path) -> PostResult:
        if not title:
            raise ValueError("title is required")
        if not body:
            raise ValueError("body is required")
        image_path = Path(image_path)
        if not image_path.exists():
            raise FileNotFoundError(f"image_path does not exist: {image_path}")

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=self.headless,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            page: Page | None = None
            try:
                context = browser.new_context(
                    locale="ja-JP",
                    timezone_id="Asia/Tokyo",
                    viewport={"width": 1280, "height": 800},
                )
                page = context.new_page()
                page.set_default_timeout(self.timeout_ms)

                self._login(page)
                self._navigate_to_blog_new(page)
                self._fill_blog_form(page, title, body, image_path)
                final_url = self._save_as_draft(page)

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
        page.goto(SALON_BOARD_LOGIN_URL, wait_until="domcontentloaded")
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
        log.info("Going to blog list: %s", SALON_BOARD_BLOG_LIST_URL)
        page.goto(SALON_BOARD_BLOG_LIST_URL, wait_until="domcontentloaded")
        self._wait_idle(page)
        self._screenshot(page, "04_blog_list")

        # Clicking the "new post" link/button is best-effort: the list page may
        # already contain the form, or the link may be on a sub-page.
        if not self._try_click(page, BLOG_NEW_BUTTON_SELECTORS, "blog_new"):
            log.warning("No 'new post' button found; assuming form is already accessible")
        self._wait_idle(page)
        self._screenshot(page, "05_blog_new_form")

    def _fill_blog_form(
        self,
        page: Page,
        title: str,
        body: str,
        image_path: Path,
    ) -> None:
        log.info("Filling form: title_len=%d body_len=%d", len(title), len(body))

        if not self._try_fill(page, TITLE_INPUT_SELECTORS, title, "title"):
            raise RuntimeError("Could not locate title input")
        if not self._try_fill(page, BODY_TEXTAREA_SELECTORS, body, "body"):
            raise RuntimeError("Could not locate body textarea")
        self._screenshot(page, "06_form_text")

        if not self._try_set_files(page, IMAGE_INPUT_SELECTORS, image_path, "image"):
            log.warning("Image upload failed; saving draft without image")
        try:
            page.wait_for_timeout(1500)
        except Exception:  # noqa: BLE001
            pass
        self._screenshot(page, "07_form_image")

    def _save_as_draft(self, page: Page) -> str | None:
        log.info("Clicking draft-save button")
        if not self._try_click(page, DRAFT_SAVE_SELECTORS, "draft_save"):
            raise RuntimeError("Could not locate draft-save button")
        self._wait_idle(page)
        self._screenshot(page, "08_after_draft_save")

        # Optional confirmation dialog
        if self._try_click(page, CONFIRM_DIALOG_SELECTORS, "confirm_dialog"):
            self._wait_idle(page)
            self._screenshot(page, "09_after_confirm")

        return page.url


# --- Convenience function -------------------------------------------------- #


def post_blog_as_draft(
    title: str,
    body: str,
    image_path: Path,
    *,
    headless: bool = True,
) -> PostResult:
    user_id = os.environ.get("SALON_BOARD_ID")
    password = os.environ.get("SALON_BOARD_PASSWORD")
    if not user_id or not password:
        raise RuntimeError(
            "SALON_BOARD_ID and SALON_BOARD_PASSWORD environment variables are required",
        )
    poster = SalonBoardPoster(user_id, password, headless=headless)
    return poster.post_as_draft(title, body, image_path)
