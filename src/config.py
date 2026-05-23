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

# Canonical salon signature. Always appended to every blog body so the LLM
# cannot truncate or omit it. Keep wording byte-identical to the README spec.
SALON_SIGNATURE = (
    "━━━━━━━━━━━━━━━━━━━━━━━\n"
    "KATEstageLASH(ケイトステージラッシュ) 蒲田西口店\n"
    "━━━━━━━━━━━━━━━━━━━━━━━\n"
    "◆住所〒144-0051 東京都大田区西蒲田7丁目48-10 \n"
    "◆アクセス蒲田駅西口 徒歩2分(JR京浜東北線/東急池上線/東急多摩川線)\n"
    "◆営業時間9:00~20:00\n"
    "◆定休日不定休\n"
    "◆メニューまつげパーマ/パリジェンヌラッシュリフトアイブロウ/眉毛パーマHBLラッシュアディクト\n"
    "◆ご予約ホットペッパービューティーから♪\n"
    "#大田区#JR蒲田駅#東急蒲田駅#京急蒲田駅#大井町#品川#東京#蒲田駅西口#鶴見#川崎#蒲田#大森#池上#蓮沼#川崎"
)
SIGNATURE_HORIZONTAL_RULE = "━━━"
