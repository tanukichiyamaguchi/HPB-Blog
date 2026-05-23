"""Central configuration constants and paths."""
from __future__ import annotations

from datetime import timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
PROMPTS_DIR = DATA_DIR / "prompts"
OUTPUT_DIR = PROJECT_ROOT / "output"
SCREENSHOTS_DIR = PROJECT_ROOT / "screenshots"

THEME_HISTORY_PATH = DATA_DIR / "theme_history.json"
BLOG_PROMPT_PATH = PROMPTS_DIR / "blog_prompt.md"
THEME_PROMPT_PATH = PROMPTS_DIR / "theme_prompt.md"

JST = timezone(timedelta(hours=9), name="JST")

CLAUDE_MODEL = "claude-sonnet-4-6"
GEMINI_IMAGE_MODEL = "gemini-3.1-flash-image-preview"

MENUS: tuple[str, ...] = (
    "眉毛WAX",
    "眉毛スタイリング",
    "まつげパーマ",
    "ラッシュリフト",
)

THEME_HISTORY_LOOKBACK_DAYS = 30

CLAUDE_MAX_TOKENS_THEME = 512
CLAUDE_MAX_TOKENS_BLOG = 4096
