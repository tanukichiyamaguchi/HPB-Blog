"""Entry point for the HPB blog auto-post pipeline.

Posts **3 blogs per day** at 08:15 / 12:15 / 19:15 JST (= 朝・昼・晩) with
all 3 staff members rotating across slots so every staff covers every slot
over a 3-day cycle.

Modes (selected by env var ``RUN_SALON_BOARD_POST``):
- ``skip`` (default): AI generation only — useful for manual / unit-test runs.
- ``schedule``: Generate + scheduled post for tomorrow's 3 slots via the
                お名前.com PHP relay.

Optional env vars:
- ``UPDATE_THEME_HISTORY=true``  Append themes to data/theme_history.json
                                  AFTER each successful slot.
- ``SLACK_WEBHOOK_URL``          When set, Slack notifications are sent.
- ``ALLOW_REPOST``               Override the per-slot duplicate-post guard.

Required env vars for ``schedule`` mode:
- ``SALON_BOARD_ID`` / ``SALON_BOARD_PASSWORD`` (Salon Board credentials)
- ``RELAY_URL`` / ``RELAY_SECRET``              (お名前 PHP relay endpoint)
"""
from __future__ import annotations

import logging
import os
import sys
import traceback
from dataclasses import dataclass
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
    setup_logging,
    write_json,
    write_text,
)


# ---------------- Post slot definitions ---------------- #


@dataclass(frozen=True)
class PostSlot:
    """One of the three daily publish slots (朝/昼/晩)."""
    label: str   # "morning" / "noon" / "evening"
    hour: int    # JST
    minute: int  # JST


# Three daily slots. Salon Board's reservation time picker only allows
# the 03:15–08:00 gap (maintenance window), so all three times are valid.
POST_SLOTS: tuple[PostSlot, ...] = (
    PostSlot("morning", 8, 15),
    PostSlot("noon", 12, 15),
    PostSlot("evening", 19, 15),
)


# Kept for backwards-compat with existing tests that reference the old
# single-slot 08:15 constants.
SCHEDULED_PUBLISH_HOUR = POST_SLOTS[0].hour
SCHEDULED_PUBLISH_MINUTE = POST_SLOTS[0].minute


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


def compute_publish_dt(now: datetime, slot: PostSlot) -> datetime:
    """Return the next ``slot`` time in JST that is strictly after ``now``.

    Normal cron fires at JST 22:15 → tomorrow's slot.
    If GitHub Actions is delayed past the slot time on the target day (cron
    queue backlog), we clamp forward by an extra day so we never schedule a
    post in the past (which Salon Board would reject or silently drop).
    """
    candidate = datetime.combine(
        now.date() + timedelta(days=1),
        time(slot.hour, slot.minute, 0),
        tzinfo=JST,
    )
    while candidate <= now:
        candidate = candidate + timedelta(days=1)
    return candidate


def compute_next_publish_dt(now: datetime) -> datetime:
    """Backwards-compat wrapper: returns the next morning (08:15 JST) slot."""
    return compute_publish_dt(now, POST_SLOTS[0])


def _relpath_or_abs(target: Path, parent: Path) -> str:
    try:
        return str(target.relative_to(parent))
    except ValueError:
        return str(target)


def _slot_output_dir(publish_dt: datetime, slot: PostSlot) -> Path:
    """``output/YYYY-MM-DD/<slot.label>/`` for the given publish datetime."""
    from src.config import OUTPUT_DIR
    out_dir = OUTPUT_DIR / publish_dt.strftime("%Y-%m-%d") / slot.label
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def _sentinel_path(out_dir: Path) -> Path:
    return out_dir / "salon_board_result.json"


def _already_posted_today(out_dir: Path) -> bool:
    """Return True if this slot's sentinel already records success."""
    sentinel = _sentinel_path(out_dir)
    if not sentinel.exists():
        return False
    try:
        from src.utils import read_json
        prev = read_json(sentinel)
        return bool(prev.get("success"))
    except Exception:  # noqa: BLE001
        return False


def generate_one_slot(
    slot: PostSlot,
    publish_dt: datetime,
    theme: ThemeResult,
    now: datetime,
) -> dict[str, Any]:
    """Generate blog body + image for a single slot. Writes artefacts to disk."""
    log = logging.getLogger("hpb-blog.main")
    out_dir = _slot_output_dir(publish_dt, slot)
    log.info(
        "--- generate slot %s (publish=%s, out=%s, theme=%s) ---",
        slot.label, publish_dt.isoformat(), out_dir, theme.theme,
    )

    blog = generate_blog(theme.theme)
    log.info("  Title: %s", blog.title)

    image_base = out_dir / "image"
    image = generate_image(theme.theme, theme.menu_focus, image_base)

    write_text(out_dir / "blog.txt", blog.body)
    write_text(out_dir / "title.txt", blog.title)
    write_text(out_dir / "image_prompt.txt", image.prompt)
    write_json(out_dir / "meta.json", {
        "slot": slot.label,
        "publish_at": publish_dt.isoformat(),
        "generated_at": now.isoformat(),
        "theme": theme.theme,
        "menu_focus": theme.menu_focus,
        "season": theme.season,
        "title": blog.title,
        "keywords": blog.keywords,
        "image_path": _relpath_or_abs(image.path, out_dir.parent.parent.parent),
        "image_mime_type": image.mime_type,
    })

    return {
        "slot": slot,
        "publish_at": publish_dt,
        "out_dir": out_dir,
        "theme_result": theme,
        "blog": blog,
        "image": image,
    }


def post_one_slot(slot_index: int, generation: dict[str, Any]) -> dict[str, Any]:
    """Post the just-generated content for a single slot via the relay."""
    from src.salon_board_relay import (
        DEFAULT_CATEGORY_LABEL,
        get_poster_for_date_and_slot,
        post_blog_scheduled,
    )

    log = logging.getLogger("hpb-blog.main")
    slot: PostSlot = generation["slot"]
    publish_at: datetime = generation["publish_at"]
    out_dir: Path = generation["out_dir"]
    blog = generation["blog"]
    theme_result: ThemeResult = generation["theme_result"]

    poster = get_poster_for_date_and_slot(publish_at.date(), slot_index)
    log.info(
        "Posting slot %s (poster=%s, cat=%s, menu=%s, publish=%s)",
        slot.label, poster, DEFAULT_CATEGORY_LABEL,
        theme_result.menu_focus, publish_at.isoformat(),
    )

    result = post_blog_scheduled(
        title=blog.title,
        body=blog.body,
        image_path=Path(generation["image"].path),
        publish_at=publish_at,
        poster=poster,
        category=DEFAULT_CATEGORY_LABEL,
        menu_focus=theme_result.menu_focus,
        theme=theme_result.theme,
    )

    write_json(_sentinel_path(out_dir), result.to_dict())
    log.info(
        "  → success=%s, final_url=%s",
        result.success, result.final_url,
    )
    return result.to_dict()


def _send_slot_success_notification(
    generation: dict[str, Any],
    sb_result: dict[str, Any],
    slot_index: int,
    total_slots: int,
) -> None:
    theme: ThemeResult = generation["theme_result"]
    blog = generation["blog"]
    image_path = Path(generation["image"].path)
    slot: PostSlot = generation["slot"]
    publish_at: datetime = generation["publish_at"]
    title_prefix = f"[{slot_index + 1}/{total_slots} {slot.label} {publish_at.strftime('%m/%d %H:%M')}] "
    notify_success(
        title=title_prefix + blog.title,
        theme=theme.theme,
        menu_focus=theme.menu_focus,
        image_path=image_path,
        final_url=sb_result.get("final_url"),
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


def _plan_pending_slots(now: datetime) -> list[tuple[int, PostSlot, datetime, Path]]:
    """Return the list of slots that still need a post (excludes done-sentinels).

    Each entry: (slot_index, slot, publish_dt, out_dir).
    """
    log = logging.getLogger("hpb-blog.main")
    pending: list[tuple[int, PostSlot, datetime, Path]] = []
    for i, slot in enumerate(POST_SLOTS):
        publish_dt = compute_publish_dt(now, slot)
        out_dir = _slot_output_dir(publish_dt, slot)
        if _already_posted_today(out_dir) and not _bool_env("ALLOW_REPOST"):
            log.info(
                "Slot %s for %s already succeeded; skipping (set ALLOW_REPOST=true to override)",
                slot.label, publish_dt.strftime("%Y-%m-%d %H:%M"),
            )
            continue
        pending.append((i, slot, publish_dt, out_dir))
    return pending


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
    total_slots = len(POST_SLOTS)

    pending = _plan_pending_slots(now)
    if not pending:
        log.info("All %d slots already posted for today's cycle; nothing to do.", total_slots)
        return 0
    log.info(
        "=== Pipeline start (%s) — %d/%d slots pending ===",
        now.isoformat(), len(pending), total_slots,
    )

    # Generate themes for all pending slots up-front so each new theme can
    # see the previously-chosen themes in its "既出テーマ" dedup block.
    from src.theme_generator import _load_history
    in_flight_history = list(_load_history())
    plans: list[tuple[int, PostSlot, datetime, Path, ThemeResult]] = []
    for slot_index, slot, publish_dt, out_dir in pending:
        theme = generate_theme(now=publish_dt, history_override=in_flight_history)
        log.info(
            "Theme %s: %s (menu=%s)",
            slot.label, theme.theme, theme.menu_focus,
        )
        plans.append((slot_index, slot, publish_dt, out_dir, theme))
        in_flight_history.append(theme.to_dict())

    successes = 0
    failures: list[tuple[PostSlot, str]] = []
    stage = "init"

    for slot_index, slot, publish_dt, out_dir, theme in plans:
        try:
            stage = f"generate[{slot.label}]"
            generation = generate_one_slot(slot, publish_dt, theme, now)

            stage = f"salon_board[{slot.label}]"
            sb_result: dict[str, Any] | None = None
            if mode == "schedule":
                sb_result = post_one_slot(slot_index, generation)
                if not sb_result.get("success"):
                    raise RuntimeError(
                        f"Salon Board post failed for slot {slot.label}: "
                        f"{sb_result.get('error')}"
                    )
            elif mode == "skip":
                log.info("RUN_SALON_BOARD_POST=skip; slot %s posted-step bypassed", slot.label)
            else:
                log.warning(
                    "Unknown RUN_SALON_BOARD_POST=%r (expected 'skip' or 'schedule'); "
                    "slot %s post bypassed", mode, slot.label,
                )

            stage = f"history[{slot.label}]"
            if _bool_env("UPDATE_THEME_HISTORY"):
                append_to_history(theme)
                log.info("  Appended to theme_history.json: %s", theme.theme)

            stage = f"notify[{slot.label}]"
            if sb_result is not None:
                _send_slot_success_notification(
                    generation, sb_result, slot_index, total_slots,
                )
            successes += 1
        except Exception as e:  # noqa: BLE001
            log.exception("Slot %s failed at stage=%s: %s", slot.label, stage, e)
            failures.append((slot, str(e)))
            try:
                _send_failure_notification(f"{stage} ({slot.label})", e)
            except Exception:  # noqa: BLE001
                log.exception("Failure notification itself failed; swallowing")

    log.info(
        "=== Pipeline done: %d succeeded, %d failed ===",
        successes, len(failures),
    )
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
