"""Entry point for the HPB blog auto-post pipeline.

Modes (selected by env var ``RUN_SALON_BOARD_POST``):
- ``skip`` (default): AI generation only — useful for manual / unit-test runs.
- ``schedule``: Generate + scheduled post (publishes tomorrow 08:15 JST) via
                the お名前.com PHP relay.

Optional env vars:
- ``UPDATE_THEME_HISTORY=true``  Append theme to data/theme_history.json
                                  AFTER a successful salon-board step.
- ``SLACK_WEBHOOK_URL``          When set, a Slack notification is sent.
- ``ALLOW_REPOST``               Override the per-day duplicate-post guard.

Required env vars for ``schedule`` mode:
- ``SALON_BOARD_ID`` / ``SALON_BOARD_PASSWORD`` (Salon Board credentials)
- ``RELAY_URL`` / ``RELAY_SECRET``              (お名前 PHP relay endpoint)
"""
from __future__ import annotations

import logging
import os
import sys
import traceback
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any

from src.blog_writer import generate_blog
from src.image_generator import generate_image
from src.notifier import notify_failure, notify_success
from src.theme_generator import ThemeResult, append_to_history, generate_theme
from src.utils import (
    JST,
    get_jst_now,
    get_today_output_dir,
    setup_logging,
    write_json,
    write_text,
)


# Publish next morning at 08:15 JST (HPB side handles actual publication)
SCHEDULED_PUBLISH_HOUR = 8
SCHEDULED_PUBLISH_MINUTE = 15


def _bool_env(name: str, default: bool = False) -> bool:
    """Parse env var as boolean. Unset OR empty → returns ``default``.

    (Previously empty string was hardcoded to False, which silently ignored
    a caller-supplied default and made env-var fallthrough buggy.)
    """
    val = os.environ.get(name, "").strip().lower()
    if not val:
        return default
    if val in ("1", "true", "yes", "on"):
        return True
    if val in ("0", "false", "no", "off"):
        return False
    return default


def _mode() -> str:
    return os.environ.get("RUN_SALON_BOARD_POST", "skip").strip().lower()


def compute_next_publish_dt(now: datetime) -> datetime:
    """Return the next 08:15 in JST that is strictly after ``now``.

    Normal cron fires at JST 22:15 → returns tomorrow 08:15.
    If GitHub Actions delays the run past 08:15 the next morning (e.g. cron
    queue backlog), we clamp forward by an extra day so we never schedule a
    post in the past (which Salon Board would reject or silently drop).
    """
    candidate = datetime.combine(
        now.date() + timedelta(days=1),
        time(SCHEDULED_PUBLISH_HOUR, SCHEDULED_PUBLISH_MINUTE, 0),
        tzinfo=JST,
    )
    while candidate <= now:
        candidate = candidate + timedelta(days=1)
    return candidate


def _relpath_or_abs(target: Path, parent: Path) -> str:
    try:
        return str(target.relative_to(parent))
    except ValueError:
        return str(target)


def run_generation(now: datetime | None = None) -> dict[str, Any]:
    log = logging.getLogger("hpb-blog.main")
    now = now or get_jst_now()
    out_dir = get_today_output_dir(now)

    log.info("=== HPB Blog generation start (%s) ===", now.isoformat())
    log.info("Output dir: %s", out_dir)

    theme = generate_theme(now=now)
    log.info(
        "Theme: %s (menu=%s, season=%s)",
        theme.theme, theme.menu_focus, theme.season,
    )

    blog = generate_blog(theme.theme)
    log.info("Blog title: %s", blog.title)

    image_base = out_dir / "image"
    image = generate_image(theme.theme, theme.menu_focus, image_base)

    blog_path = out_dir / "blog.txt"
    title_path = out_dir / "title.txt"
    prompt_path = out_dir / "image_prompt.txt"
    meta_path = out_dir / "meta.json"

    write_text(blog_path, blog.body)
    write_text(title_path, blog.title)
    write_text(prompt_path, image.prompt)
    meta = {
        "date": theme.date,
        "generated_at": now.isoformat(),
        "theme": theme.theme,
        "menu_focus": theme.menu_focus,
        "season": theme.season,
        "title": blog.title,
        "keywords": blog.keywords,
        "image_path": _relpath_or_abs(image.path, out_dir.parent.parent),
        "image_mime_type": image.mime_type,
    }
    write_json(meta_path, meta)

    log.info("=== Generation complete ===")
    log.info("  out_dir : %s", out_dir)
    log.info("  blog    : %s", blog_path)
    log.info("  image   : %s", image.path)

    return {
        "out_dir": str(out_dir),
        "theme_result": theme,
        "blog": blog,
        "image": image,
        "title": blog.title,
        "blog_path": str(blog_path),
        "image_path": str(image.path),
        "meta_path": str(meta_path),
    }


def _sentinel_path(out_dir: Path) -> Path:
    return out_dir / "salon_board_result.json"


def _already_posted_today(out_dir: Path) -> bool:
    """Return True if today's salon_board_result.json already records success."""
    sentinel = _sentinel_path(out_dir)
    if not sentinel.exists():
        return False
    try:
        from src.utils import read_json
        prev = read_json(sentinel)
        return bool(prev.get("success"))
    except Exception:  # noqa: BLE001
        return False


def run_salon_board(generation: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Post the generated content to Salon Board as a scheduled post (via relay)."""
    from src.salon_board_relay import (
        DEFAULT_CATEGORY_LABEL,
        get_poster_for_date,
        post_blog_scheduled,
    )

    log = logging.getLogger("hpb-blog.main")
    out_dir = Path(generation["out_dir"])

    # Duplicate-post guard: if today's sentinel already records a success,
    # skip the Salon Board step. Protects against double-fire (manual dispatch
    # on a cron-firing day, or a workflow re-run after a partial failure).
    if _already_posted_today(out_dir) and not _bool_env("ALLOW_REPOST"):
        log.warning(
            "Today's salon_board_result.json already records success; skipping repost. "
            "Set ALLOW_REPOST=true to override.",
        )
        from src.utils import read_json
        return read_json(_sentinel_path(out_dir))

    blog = generation["blog"]
    image_path = Path(generation["image_path"])
    poster = get_poster_for_date(now.date())
    publish_at = compute_next_publish_dt(now)
    log.info(
        "=== Salon Board: SCHEDULE mode (poster=%s, cat=%s, publish_at=%s) ===",
        poster, DEFAULT_CATEGORY_LABEL, publish_at.isoformat(),
    )

    result = post_blog_scheduled(
        title=blog.title,
        body=blog.body,
        image_path=image_path,
        publish_at=publish_at,
        poster=poster,
        category=DEFAULT_CATEGORY_LABEL,
    )

    write_json(_sentinel_path(out_dir), result.to_dict())
    log.info("Salon Board: success=%s final_url=%s", result.success, result.final_url)
    return result.to_dict()


def maybe_update_history(theme: ThemeResult) -> None:
    if _bool_env("UPDATE_THEME_HISTORY"):
        log = logging.getLogger("hpb-blog.main")
        append_to_history(theme)
        log.info("Appended to theme_history.json: %s", theme.theme)


def _send_success_notification(generation: dict[str, Any], sb_result: dict[str, Any] | None) -> None:
    theme: ThemeResult = generation["theme_result"]
    blog = generation["blog"]
    image_path = Path(generation["image_path"])
    final_url = sb_result.get("final_url") if sb_result else None
    notify_success(
        title=blog.title,
        theme=theme.theme,
        menu_focus=theme.menu_focus,
        image_path=image_path,
        final_url=final_url,
    )


def _send_failure_notification(stage: str, exc: BaseException) -> None:
    run_url = os.environ.get("GITHUB_RUN_URL")
    message = f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}"
    notify_failure(error_message=message, stage=stage, run_url=run_url)


def _log_config(log: logging.Logger) -> None:
    """Dump key config at startup so hangs are diagnosable from CI logs."""
    from src.config import (
        ANTHROPIC_TIMEOUT_SEC,
        API_RETRY_ATTEMPTS,
        CLAUDE_MODEL,
        GEMINI_IMAGE_MODEL,
        GEMINI_TIMEOUT_SEC,
    )
    log.info("--- Runtime config ---")
    log.info("  CLAUDE_MODEL          = %s", CLAUDE_MODEL)
    log.info("  GEMINI_IMAGE_MODEL    = %s", GEMINI_IMAGE_MODEL)
    log.info("  ANTHROPIC_TIMEOUT_SEC = %s", ANTHROPIC_TIMEOUT_SEC)
    log.info("  GEMINI_TIMEOUT_SEC    = %s", GEMINI_TIMEOUT_SEC)
    log.info("  API_RETRY_ATTEMPTS    = %s", API_RETRY_ATTEMPTS)
    log.info("  RUN_SALON_BOARD_POST  = %s", _mode())
    log.info("  UPDATE_THEME_HISTORY  = %s", os.environ.get("UPDATE_THEME_HISTORY", ""))
    log.info("----------------------")


def main() -> int:
    # Load .env if present (PC / local-dev workflow). Optional — in CI we rely
    # on GitHub Secrets being set directly in the env.
    try:
        from dotenv import load_dotenv  # type: ignore[import-not-found]
        load_dotenv()
    except ImportError:
        pass

    log = setup_logging()
    _log_config(log)
    mode = _mode()
    now = get_jst_now()

    generation: dict[str, Any] | None = None
    sb_result: dict[str, Any] | None = None
    stage = "generation"

    try:
        generation = run_generation(now=now)
        stage = "salon_board"

        if mode == "schedule":
            sb_result = run_salon_board(generation, now)
            if not sb_result.get("success"):
                raise RuntimeError(f"Salon Board step failed: {sb_result.get('error')}")
        elif mode == "skip":
            log.info("RUN_SALON_BOARD_POST=skip; salon-board step bypassed")
        else:
            log.warning(
                "Unknown RUN_SALON_BOARD_POST=%r (expected 'skip' or 'schedule'); "
                "bypassing salon-board step",
                mode,
            )

        stage = "post_steps"
        # Append to history only after the salon-board step succeeded (or was skipped intentionally).
        maybe_update_history(generation["theme_result"])

        stage = "notify"
        _send_success_notification(generation, sb_result)
        log.info("=== Pipeline succeeded ===")
        return 0
    except Exception as e:  # noqa: BLE001
        log.exception("Pipeline failed at stage=%s: %s", stage, e)
        try:
            _send_failure_notification(stage, e)
        except Exception:  # noqa: BLE001
            log.exception("Failure notification itself failed; swallowing")
        return 1


if __name__ == "__main__":
    sys.exit(main())
