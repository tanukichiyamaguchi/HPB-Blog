"""Entry point.

Phase 2: AI generation only (theme/blog/image).
Phase 3: Adds salon-board draft posting when ``RUN_SALON_BOARD_POST=draft``.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

from src.blog_writer import generate_blog
from src.image_generator import generate_image
from src.theme_generator import generate_theme
from src.utils import (
    get_jst_now,
    get_today_output_dir,
    setup_logging,
    write_json,
    write_text,
)


def _relpath_or_abs(target: Path, parent: Path) -> str:
    """Return target relative to parent if possible, else absolute."""
    try:
        return str(target.relative_to(parent))
    except ValueError:
        return str(target)


def run_generation() -> dict[str, Any]:
    log = setup_logging()
    now = get_jst_now()
    out_dir = get_today_output_dir(now)

    log.info("=== HPB Blog generation start (%s) ===", now.isoformat())
    log.info("Output dir: %s", out_dir)

    theme = generate_theme(now=now)
    log.info(
        "Theme decided: %s (menu=%s, season=%s)",
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
    meta: dict[str, Any] = {
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

    log.info("=== Generation completed ===")
    log.info("  out_dir : %s", out_dir)
    log.info("  blog    : %s", blog_path)
    log.info("  title   : %s", title_path)
    log.info("  image   : %s", image.path)
    log.info("  meta    : %s", meta_path)

    return {
        "out_dir": str(out_dir),
        "theme": theme.to_dict(),
        "blog": blog,
        "image": image,
        "title": blog.title,
        "blog_path": str(blog_path),
        "image_path": str(image.path),
        "meta_path": str(meta_path),
    }


def run_salon_board_draft(generation: dict[str, Any]) -> dict[str, Any]:
    """Post the generated content to Salon Board as DRAFT (Phase 3)."""
    # Imported lazily so Phase 2 runs (and unit tests) don't require Playwright.
    from src.salon_board_poster import post_blog_as_draft

    log = logging.getLogger("hpb-blog.main")
    blog = generation["blog"]
    image_path = Path(generation["image_path"])

    log.info("=== Salon Board draft posting start ===")
    result = post_blog_as_draft(
        title=blog.title,
        body=blog.body,
        image_path=image_path,
        headless=True,
    )
    log.info(
        "=== Salon Board draft posting done: success=%s, final_url=%s ===",
        result.success, result.final_url,
    )

    # Persist a summary of what happened
    out_dir = Path(generation["out_dir"])
    write_json(out_dir / "salon_board_result.json", result.to_dict())
    return result.to_dict()


def main() -> int:
    log = setup_logging()
    mode = os.environ.get("RUN_SALON_BOARD_POST", "skip").strip().lower()
    try:
        generation = run_generation()

        if mode == "draft":
            sb_result = run_salon_board_draft(generation)
            if not sb_result.get("success"):
                log.error("Salon Board posting failed: %s", sb_result.get("error"))
                return 2
        elif mode == "skip":
            log.info("RUN_SALON_BOARD_POST=skip; salon-board step bypassed")
        else:
            log.warning("Unknown RUN_SALON_BOARD_POST=%r; bypassing salon-board step", mode)
    except Exception as e:  # noqa: BLE001
        log.exception("Pipeline failed: %s", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
