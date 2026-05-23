"""Generate a daily blog theme via Claude with menu LRU rotation and 30-day dedup."""
from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

from anthropic import Anthropic

from src.config import (
    CLAUDE_MAX_TOKENS_THEME,
    CLAUDE_MODEL,
    MENUS,
    THEME_HISTORY_LOOKBACK_DAYS,
    THEME_HISTORY_PATH,
    THEME_PROMPT_PATH,
)
from src.utils import (
    get_jst_now,
    get_season,
    read_json,
    read_text,
    retry,
    write_json,
)

log = logging.getLogger(__name__)


@dataclass
class ThemeResult:
    date: str
    theme: str
    menu_focus: str
    season: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _load_history() -> list[dict[str, Any]]:
    if not THEME_HISTORY_PATH.exists():
        return []
    raw = read_json(THEME_HISTORY_PATH)
    return raw if isinstance(raw, list) else []


def _select_menu_focus(history: list[dict[str, Any]]) -> str:
    """Return the least-recently-used menu. Never-used menus take priority."""
    last_used: dict[str, str] = {}
    for entry in history:
        menu = entry.get("menu_focus")
        date_str = entry.get("date")
        if menu in MENUS and date_str:
            existing = last_used.get(menu)
            if existing is None or date_str > existing:
                last_used[menu] = date_str
    # Never-used menu → empty string sorts first
    return min(MENUS, key=lambda m: last_used.get(m, ""))


def _recent_themes(
    history: list[dict[str, Any]],
    today: datetime,
    lookback_days: int,
) -> list[str]:
    cutoff = (today - timedelta(days=lookback_days)).date()
    themes: list[str] = []
    for entry in history:
        date_str = entry.get("date")
        theme = entry.get("theme")
        if not date_str or not theme:
            continue
        try:
            entry_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if entry_date >= cutoff:
            themes.append(theme)
    return themes


def _build_user_prompt(today: datetime, menu_focus: str, recent: list[str]) -> str:
    template = read_text(THEME_PROMPT_PATH)
    recent_block = "\n".join(f"- {t}" for t in recent) if recent else "（なし）"
    return (
        template
        .replace("{{TODAY_DATE}}", today.strftime("%Y-%m-%d"))
        .replace("{{SEASON}}", get_season(today.month))
        .replace("{{MENU_FOCUS}}", menu_focus)
        .replace("{{RECENT_THEMES}}", recent_block)
    )


def _extract_text(message: Any) -> str:
    parts: list[str] = []
    for block in getattr(message, "content", []) or []:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


def _sanitize_theme(text: str) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    candidate = lines[0] if lines else text.strip()
    # Strip common surrounding markers
    return candidate.strip("「」『』\"' \t-•*").strip()


def append_to_history(result: ThemeResult) -> None:
    history = _load_history()
    history.append(result.to_dict())
    write_json(THEME_HISTORY_PATH, history)


def generate_theme(
    client: Anthropic | None = None,
    *,
    now: datetime | None = None,
    history_override: list[dict[str, Any]] | None = None,
) -> ThemeResult:
    today = now or get_jst_now()
    history = history_override if history_override is not None else _load_history()
    menu_focus = _select_menu_focus(history)
    recent = _recent_themes(history, today, THEME_HISTORY_LOOKBACK_DAYS)
    user_prompt = _build_user_prompt(today, menu_focus, recent)
    season = get_season(today.month)

    log.info(
        "Generating theme for %s (menu_focus=%s, season=%s, recent_count=%d)",
        today.strftime("%Y-%m-%d"), menu_focus, season, len(recent),
    )

    api_client = client or Anthropic(api_key=_require_api_key())

    def _call() -> Any:
        return api_client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=CLAUDE_MAX_TOKENS_THEME,
            messages=[{"role": "user", "content": user_prompt}],
        )

    message = retry(_call, max_attempts=3, base_delay=2.0, logger=log)
    raw = _extract_text(message)
    theme = _sanitize_theme(raw)
    if not theme:
        raise RuntimeError(f"Claude returned empty theme (raw={raw!r})")

    return ThemeResult(
        date=today.strftime("%Y-%m-%d"),
        theme=theme,
        menu_focus=menu_focus,
        season=season,
    )


def _require_api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY environment variable is not set")
    return key
