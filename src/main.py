"""Entry point. Phase 2: runs AI generation only (theme/blog/image)."""
from __future__ import annotations

import logging
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


def run_generation() -> dict[str, Any]:
    log = setup_logging()
    now = get_jst_now()
    out_dir = get_today_output_dir(now)

    log.info("=== HPB Blog generation start (%s) ===", now.isoformat())
    log.info("Output dir: %s", out_dir)

    theme = generate_theme(now=now)
    log.info("Theme decided: %s (menu=%s, season=%s)", theme.theme, theme.menu_focus, theme.season)

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
        "image_path": str(image.path.relative_to(out_dir.parent.parent))
            if out_dir.parent.parent in image.path.parents
            else str(image.path),
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
        "title": blog.title,
        "blog_path": str(blog_path),
        "image_path": str(image.path),
        "meta_path": str(meta_path),
    }


def main() -> int:
    try:
        run_generation()
    except Exception as e:
        log = logging.getLogger("hpb-blog.main")
        log.exception("Generation failed: %s", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
