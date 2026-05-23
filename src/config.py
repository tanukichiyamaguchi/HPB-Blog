"""Central configuration constants and paths."""
from __future__ import annotations

import os
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

def _env_or(name: str, default: str) -> str:
    """Return env var value, falling back to default for unset OR empty strings.

    Treating empty as "unset" lets workflows pass ``vars.X`` unconditionally;
    when the repo variable is not configured GitHub interpolates the empty string,
    which would otherwise override our default and break the call.
    """
    val = os.environ.get(name, "").strip()
    return val or default


# Model names — overridable via env so we can switch without code changes when
# a model is renamed or unavailable.
CLAUDE_MODEL = _env_or("CLAUDE_MODEL", "claude-sonnet-4-6")
GEMINI_IMAGE_MODEL = _env_or("GEMINI_IMAGE_MODEL", "gemini-3.1-flash-image-preview")

# API timeouts (seconds). The Anthropic SDK defaults to 600s; we cap much tighter
# so a hanging request can't blow past the workflow timeout-minutes budget.
ANTHROPIC_TIMEOUT_SEC = float(_env_or("ANTHROPIC_TIMEOUT_SEC", "120"))
GEMINI_TIMEOUT_SEC = float(_env_or("GEMINI_TIMEOUT_SEC", "180"))

# Retries are layered on top of API timeouts. Keep modest so a model outage
# doesn't multiply into a workflow timeout.
API_RETRY_ATTEMPTS = int(_env_or("API_RETRY_ATTEMPTS", "2"))
API_RETRY_BASE_DELAY_SEC = float(_env_or("API_RETRY_BASE_DELAY_SEC", "2.0"))

MENUS: tuple[str, ...] = (
    "眉毛WAX",
    "眉毛スタイリング",
    "まつげパーマ",
    "ラッシュリフト",
)

THEME_HISTORY_LOOKBACK_DAYS = 30

CLAUDE_MAX_TOKENS_THEME = 512
CLAUDE_MAX_TOKENS_BLOG = 4096

# HPB title input has a character cap. Spec says 全角 25 chars; we count every
# code point as 1 unit (strict upper bound, safe regardless of how HPB counts
# half-width chars).
MAX_TITLE_LENGTH = 25

# Total body length cap (LLM-generated content + auto-appended signature).
# User requirement: 絶対に 1000 文字以下.
MAX_BODY_LENGTH = 1000

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
