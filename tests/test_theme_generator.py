from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.config import MENUS
from src.theme_generator import (
    ThemeResult,
    _build_user_prompt,
    _recent_themes,
    _sanitize_theme,
    _select_menu_focus,
    generate_theme,
)


def test_select_menu_focus_empty_history_returns_first_menu():
    # With empty history all menus tie at "" — min() returns the first one
    assert _select_menu_focus([]) == MENUS[0]


def test_select_menu_focus_picks_never_used_menu():
    history = [
        {"date": "2026-05-01", "menu_focus": "眉毛WAX", "theme": "x"},
        {"date": "2026-05-02", "menu_focus": "眉毛スタイリング", "theme": "y"},
        {"date": "2026-05-03", "menu_focus": "まつげパーマ", "theme": "z"},
    ]
    # ラッシュリフト never used → should be picked
    assert _select_menu_focus(history) == "ラッシュリフト"


def test_select_menu_focus_picks_lru_when_all_used():
    history = [
        {"date": "2026-05-01", "menu_focus": "眉毛WAX", "theme": "a"},
        {"date": "2026-05-02", "menu_focus": "眉毛スタイリング", "theme": "b"},
        {"date": "2026-05-03", "menu_focus": "まつげパーマ", "theme": "c"},
        {"date": "2026-05-04", "menu_focus": "ラッシュリフト", "theme": "d"},
        {"date": "2026-05-05", "menu_focus": "眉毛スタイリング", "theme": "e"},  # used twice
    ]
    # 眉毛WAX is the oldest still on 2026-05-01 → LRU
    assert _select_menu_focus(history) == "眉毛WAX"


def test_select_menu_focus_ignores_unknown_menu():
    history = [
        {"date": "2026-05-01", "menu_focus": "unknown", "theme": "x"},
    ]
    assert _select_menu_focus(history) == MENUS[0]


def test_recent_themes_filters_by_lookback():
    today = datetime(2026, 6, 1)
    history = [
        {"date": "2026-04-01", "theme": "old1"},  # 61 days ago — out
        {"date": "2026-05-15", "theme": "recent1"},  # 17 days ago — in
        {"date": "2026-05-30", "theme": "recent2"},  # 2 days ago — in
        {"date": "invalid", "theme": "broken"},  # skipped
        {"date": "2026-06-01", "theme": "today"},  # in
    ]
    recent = _recent_themes(history, today, lookback_days=30)
    assert "old1" not in recent
    assert "recent1" in recent
    assert "recent2" in recent
    assert "today" in recent
    assert "broken" not in recent


def test_recent_themes_empty_history():
    assert _recent_themes([], datetime(2026, 6, 1), 30) == []


def test_build_user_prompt_replaces_placeholders(tmp_path, monkeypatch):
    # Create a temp prompt file and patch THEME_PROMPT_PATH
    sample = (
        "今日: {{TODAY_DATE}}（{{SEASON}}）\n"
        "メニュー: {{MENU_FOCUS}}\n"
        "履歴:\n{{RECENT_THEMES}}\n"
    )
    p = tmp_path / "theme_prompt.md"
    p.write_text(sample, encoding="utf-8")
    monkeypatch.setattr("src.theme_generator.THEME_PROMPT_PATH", p)

    result = _build_user_prompt(
        datetime(2026, 6, 15),
        "眉毛WAX",
        ["梅雨対策の眉メイク", "夏に向けたまつげケア"],
    )
    assert "2026-06-15" in result
    assert "夏" in result
    assert "眉毛WAX" in result
    assert "- 梅雨対策の眉メイク" in result
    assert "- 夏に向けたまつげケア" in result


def test_build_user_prompt_no_recent_themes(tmp_path, monkeypatch):
    sample = "履歴:\n{{RECENT_THEMES}}\n"
    p = tmp_path / "theme_prompt.md"
    p.write_text(sample, encoding="utf-8")
    monkeypatch.setattr("src.theme_generator.THEME_PROMPT_PATH", p)

    result = _build_user_prompt(datetime(2026, 6, 15), "眉毛WAX", [])
    assert "（なし）" in result


def test_sanitize_theme_strips_quotes_and_markers():
    assert _sanitize_theme("「梅雨に映える眉のスタイリング」") == "梅雨に映える眉のスタイリング"
    assert _sanitize_theme("- 梅雨に映える眉") == "梅雨に映える眉"
    assert _sanitize_theme("**梅雨に映える眉**") == "梅雨に映える眉"


def test_sanitize_theme_picks_first_nonempty_line():
    assert _sanitize_theme("\n  \n梅雨に映える眉\n二行目\n") == "梅雨に映える眉"


def test_generate_theme_with_mocked_client(monkeypatch, tmp_path):
    # Mock the prompt file
    sample = "{{TODAY_DATE}} {{SEASON}} {{MENU_FOCUS}} {{RECENT_THEMES}}"
    p = tmp_path / "theme_prompt.md"
    p.write_text(sample, encoding="utf-8")
    monkeypatch.setattr("src.theme_generator.THEME_PROMPT_PATH", p)

    # Mock Anthropic client
    mock_msg = SimpleNamespace(content=[SimpleNamespace(text="梅雨に映える眉メイクのコツ")])
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_msg

    result = generate_theme(
        client=mock_client,
        now=datetime(2026, 6, 15),
        history_override=[],
    )
    assert isinstance(result, ThemeResult)
    assert result.theme == "梅雨に映える眉メイクのコツ"
    assert result.season == "夏"
    assert result.menu_focus == MENUS[0]
    assert result.date == "2026-06-15"
    mock_client.messages.create.assert_called_once()


def test_generate_theme_raises_on_empty_response(monkeypatch, tmp_path):
    p = tmp_path / "theme_prompt.md"
    p.write_text("{{TODAY_DATE}}", encoding="utf-8")
    monkeypatch.setattr("src.theme_generator.THEME_PROMPT_PATH", p)

    mock_msg = SimpleNamespace(content=[SimpleNamespace(text="")])
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_msg

    with pytest.raises(RuntimeError, match="empty theme"):
        generate_theme(client=mock_client, now=datetime(2026, 6, 15), history_override=[])


def test_generate_themes_batch_returns_n_unique_results(monkeypatch, tmp_path):
    """Batch generation returns N results with within-batch uniqueness via in-flight history."""
    from src.theme_generator import generate_themes_batch

    p = tmp_path / "theme_prompt.md"
    p.write_text("{{TODAY_DATE}} {{MENU_FOCUS}} {{RECENT_THEMES}}", encoding="utf-8")
    monkeypatch.setattr("src.theme_generator.THEME_PROMPT_PATH", p)

    # Mock client returns deterministic themes; we just verify the function
    # calls .messages.create() exactly count times and accumulates results.
    counter = {"i": 0}

    def fake_create(**kwargs):
        counter["i"] += 1
        return SimpleNamespace(content=[SimpleNamespace(text=f"テーマ{counter['i']}")])

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = fake_create

    results = generate_themes_batch(
        count=7,
        base_now=datetime(2026, 5, 23),
        history_override=[],
        client=mock_client,
    )
    assert len(results) == 7
    # Each result is a ThemeResult with sequential dates
    dates = [r.date for r in results]
    assert dates == [f"2026-05-{23 + i}" for i in range(7)]
    # Themes are distinct (because our mock returns distinct counter values)
    assert len({r.theme for r in results}) == 7


def test_generate_themes_batch_zero_count_returns_empty(monkeypatch, tmp_path):
    from src.theme_generator import generate_themes_batch

    p = tmp_path / "theme_prompt.md"
    p.write_text("placeholder", encoding="utf-8")
    monkeypatch.setattr("src.theme_generator.THEME_PROMPT_PATH", p)

    assert generate_themes_batch(0, base_now=datetime(2026, 5, 23), client=MagicMock()) == []
    assert generate_themes_batch(-1, base_now=datetime(2026, 5, 23), client=MagicMock()) == []


def test_generate_themes_batch_dedupes_within_batch(monkeypatch, tmp_path):
    """Subsequent iterations must see prior batch themes in the prompt (recent_themes)."""
    from src.theme_generator import generate_themes_batch

    p = tmp_path / "theme_prompt.md"
    p.write_text("{{RECENT_THEMES}}", encoding="utf-8")
    monkeypatch.setattr("src.theme_generator.THEME_PROMPT_PATH", p)

    seen_recent_blocks: list[str] = []
    counter = {"i": 0}

    def fake_create(**kwargs):
        counter["i"] += 1
        # Capture the user prompt to verify in-flight history is propagated
        seen_recent_blocks.append(kwargs["messages"][0]["content"])
        return SimpleNamespace(content=[SimpleNamespace(text=f"unique{counter['i']}")])

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = fake_create

    generate_themes_batch(
        count=3,
        base_now=datetime(2026, 5, 23),
        history_override=[],
        client=mock_client,
    )
    # First iteration has empty recent (just "（なし）")
    assert "（なし）" in seen_recent_blocks[0]
    # Second iteration sees first theme
    assert "unique1" in seen_recent_blocks[1]
    # Third iteration sees both prior themes
    assert "unique1" in seen_recent_blocks[2]
    assert "unique2" in seen_recent_blocks[2]
