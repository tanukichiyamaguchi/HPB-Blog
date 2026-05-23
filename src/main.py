"""Entry point for the HPB blog auto-post pipeline.

Modes (selected by env var ``RUN_SALON_BOARD_POST``):
- ``skip`` (default): AI generation only.
- ``draft``: Generate + save as draft (Phase 3 verification).
- ``schedule``: Generate + scheduled post (publishes tomorrow 08:15 JST).
- ``weekly``: Generate ``WEEKLY_BATCH_DAYS`` (default 7) days of content and
              schedule them all in one Salon Board login. Designed for the
              user-PC workflow: salon owner runs once a week, all 7 posts are
              scheduled in advance.

Optional env vars:
- ``UPDATE_THEME_HISTORY=true``  Append theme(s) to data/theme_history.json
                                  AFTER a successful salon-board step.
- ``SLACK_WEBHOOK_URL``          When set, a Slack notification is sent.
- ``WEEKLY_BATCH_DAYS``          Days to generate in weekly mode (default 7).
- ``ALLOW_REPOST``               Override the per-day duplicate-post guard.
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


def run_salon_board(generation: dict[str, Any], mode: str, now: datetime) -> dict[str, Any]:
    """Post the generated content to Salon Board (draft or schedule)."""
    # Lazy import so unit tests don't require Playwright to be installed at module level.
    from src.salon_board_poster import (
        DEFAULT_CATEGORY,
        get_poster_for_date,
        post_blog_as_draft,
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
    category = DEFAULT_CATEGORY
    log.info("Salon Board params: poster=%s, category=%s", poster, category)

    if mode == "draft":
        log.info("=== Salon Board: DRAFT mode ===")
        result = post_blog_as_draft(
            blog.title, blog.body, image_path,
            poster=poster, category=category, headless=True,
        )
    elif mode == "schedule":
        publish_at = compute_next_publish_dt(now)
        log.info("=== Salon Board: SCHEDULE mode (publish_at=%s) ===", publish_at.isoformat())
        result = post_blog_scheduled(
            blog.title, blog.body, image_path, publish_at,
            poster=poster, category=category, headless=True,
        )
    else:
        raise ValueError(f"Unknown salon-board mode: {mode!r}")

    # out_dir was already resolved above for the duplicate-post guard.
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


# ---------------- Weekly batch mode ---------------- #


def _publish_dt_for_offset(base_now: datetime, day_offset: int) -> datetime:
    """Compute the publish datetime for day base_now + (day_offset+1) at 08:15 JST.

    day_offset 0 → tomorrow 08:15, 1 → 2 days from now 08:15, ..., 6 → 7 days.
    """
    target_date = base_now.date() + timedelta(days=day_offset + 1)
    return datetime.combine(
        target_date,
        time(SCHEDULED_PUBLISH_HOUR, SCHEDULED_PUBLISH_MINUTE, 0),
        tzinfo=JST,
    )


def _generate_one_day(theme_result, now_for_day: datetime, out_dir: Path) -> dict[str, Any]:
    """Generate body + image for a single day's theme; write artifacts to out_dir."""
    log = logging.getLogger("hpb-blog.main")
    log.info("Generating body+image for %s (theme=%s)", out_dir.name, theme_result.theme)
    blog = generate_blog(theme_result.theme)
    image_base = out_dir / "image"
    image = generate_image(theme_result.theme, theme_result.menu_focus, image_base)

    write_text(out_dir / "blog.txt", blog.body)
    write_text(out_dir / "title.txt", blog.title)
    write_text(out_dir / "image_prompt.txt", image.prompt)
    write_json(out_dir / "meta.json", {
        "date": out_dir.name,
        "generated_at": now_for_day.isoformat(),
        "theme": theme_result.theme,
        "menu_focus": theme_result.menu_focus,
        "season": theme_result.season,
        "title": blog.title,
        "keywords": blog.keywords,
        "image_path": str(image.path),
        "image_mime_type": image.mime_type,
    })
    return {"theme_result": theme_result, "blog": blog, "image": image, "out_dir": out_dir}


def run_weekly_batch(days: int, now: datetime) -> int:
    """Generate ``days`` posts and schedule them all in a single Salon Board session."""
    log = logging.getLogger("hpb-blog.main")
    from src.config import OUTPUT_DIR
    from src.salon_board_poster import (
        BatchPostItem,
        DEFAULT_CATEGORY,
        SalonBoardPoster,
        get_poster_for_date,
    )
    from src.theme_generator import _load_history, generate_themes_batch

    log.info("=== Weekly batch: %d days starting %s ===", days, now.strftime("%Y-%m-%d"))

    # Skip days that already have a successful sentinel (idempotency / re-run safety)
    pending: list[tuple[int, Path, datetime]] = []
    for i in range(days):
        publish_dt = _publish_dt_for_offset(now, i)
        out_dir = OUTPUT_DIR / publish_dt.strftime("%Y-%m-%d")
        out_dir.mkdir(parents=True, exist_ok=True)
        if _already_posted_today(out_dir) and not _bool_env("ALLOW_REPOST"):
            log.info("Day %s already has a successful post; skipping", out_dir.name)
            continue
        pending.append((i, out_dir, publish_dt))

    if not pending:
        log.info("All %d days already posted; nothing to do", days)
        return 0

    # 1) Generate themes for pending days (in-flight dedup baked in)
    base_history = _load_history()
    themes = generate_themes_batch(
        count=len(pending),
        base_now=now,
        history_override=base_history,
    )
    log.info("Generated %d themes:", len(themes))
    for t in themes:
        log.info("  - %s: %s (menu=%s)", t.date, t.theme, t.menu_focus)

    # 2) Generate body + image for each
    generations: list[dict[str, Any]] = []
    for (i, out_dir, publish_dt), theme in zip(pending, themes):
        now_for_day = now + timedelta(days=i)
        # Override date/menu_focus computed by themes_batch with our concrete publish date
        theme.date = out_dir.name
        gen = _generate_one_day(theme, now_for_day, out_dir)
        generations.append({**gen, "publish_dt": publish_dt})

    # 3) Build batch items
    user_id = os.environ.get("SALON_BOARD_ID")
    password = os.environ.get("SALON_BOARD_PASSWORD")
    if not user_id or not password:
        raise RuntimeError("SALON_BOARD_ID and SALON_BOARD_PASSWORD must be set")

    batch_items: list[BatchPostItem] = []
    for gen in generations:
        publish_dt: datetime = gen["publish_dt"]
        poster = get_poster_for_date(publish_dt.date())
        batch_items.append(BatchPostItem(
            title=gen["blog"].title,
            body=gen["blog"].body,
            image_path=Path(gen["image"].path),
            scheduled_dt=publish_dt,
            poster=poster,
            category=DEFAULT_CATEGORY,
            label=publish_dt.strftime("%Y%m%d"),
        ))

    log.info("Posting batch of %d to Salon Board (single login)", len(batch_items))
    poster_obj = SalonBoardPoster(user_id, password, headless=_bool_env("SB_HEADLESS", default=True))
    results = poster_obj.post_batch_scheduled(batch_items)

    # 4) Persist per-day sentinels, append history for successes
    history_appends = 0
    for gen, result in zip(generations, results):
        out_dir: Path = gen["out_dir"]
        write_json(out_dir / "salon_board_result.json", result.to_dict())
        if result.success and _bool_env("UPDATE_THEME_HISTORY"):
            from src.theme_generator import append_to_history
            append_to_history(gen["theme_result"])
            history_appends += 1
    log.info("Persisted %d sentinels, appended %d history entries", len(generations), history_appends)

    # 5) Summary notification
    success_count = sum(1 for r in results if r.success)
    failure_count = len(results) - success_count
    log.info("=== Weekly batch result: %d succeeded, %d failed ===", success_count, failure_count)
    try:
        if success_count > 0:
            first_success = next((g for g, r in zip(generations, results) if r.success), None)
            if first_success:
                notify_success(
                    title=f"週次バッチ: {success_count}/{len(results)} 件投稿予約完了",
                    theme=first_success["theme_result"].theme,
                    menu_focus=first_success["theme_result"].menu_focus,
                    image_path=Path(first_success["image"].path),
                )
        if failure_count > 0:
            errors = "\n".join(
                f"- {g['out_dir'].name}: {r.error}"
                for g, r in zip(generations, results) if not r.success
            )
            notify_failure(
                error_message=f"週次バッチで {failure_count}/{len(results)} 件失敗:\n{errors}",
                stage="weekly_batch",
            )
    except Exception:  # noqa: BLE001
        log.exception("Notification failed; swallowing")

    return 0 if failure_count == 0 else 1


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

    # Weekly batch mode is structurally different from single-day modes:
    # it generates and posts N days in one shot rather than one day.
    if mode == "weekly":
        try:
            days = int(os.environ.get("WEEKLY_BATCH_DAYS", "7"))
        except ValueError:
            days = 7
        try:
            return run_weekly_batch(days, now)
        except Exception as e:  # noqa: BLE001
            log.exception("Weekly batch failed: %s", e)
            try:
                _send_failure_notification("weekly_batch", e)
            except Exception:  # noqa: BLE001
                log.exception("Failure notification itself failed; swallowing")
            return 1

    generation: dict[str, Any] | None = None
    sb_result: dict[str, Any] | None = None
    stage = "generation"

    try:
        generation = run_generation(now=now)
        stage = "salon_board"

        if mode in ("draft", "schedule"):
            sb_result = run_salon_board(generation, mode, now)
            if not sb_result.get("success"):
                raise RuntimeError(f"Salon Board step failed: {sb_result.get('error')}")
        elif mode == "skip":
            log.info("RUN_SALON_BOARD_POST=skip; salon-board step bypassed")
        else:
            log.warning("Unknown RUN_SALON_BOARD_POST=%r; bypassing salon-board step", mode)

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
