from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.blog_writer import (
    BlogPost,
    _parse_keywords,
    _parse_sections,
    ensure_signature,
    generate_blog,
    parse_blog,
)
from src.config import SALON_SIGNATURE


SAMPLE_OUTPUT = """---
◆タイトル：
蒲田駅西口で眉毛WAX♪理想の眉が手に入るサロン

◆使用キーワード：
蒲田、蒲田駅西口、眉毛WAX、眉毛、アイブロウ

◆本文：
【1】はじめに
蒲田駅西口にあるKATEstageLASHです♪
眉毛WAXは、産毛から太い毛までスッキリ整えられる施術。

【2】眉毛WAXのこだわり
お客様の骨格と表情に合わせて、自然なアーチを描きます。

【3】こんな方におすすめ
眉毛を整えたいけど、自分では難しいと感じる方にぴったり。

気になる方はぜひお気軽にご予約ください♪
HPBからのご予約がスムーズです。

━━━━━━━━━━━━━━━━━━━━━━━
KATEstageLASH(ケイトステージラッシュ) 蒲田西口店
━━━━━━━━━━━━━━━━━━━━━━━
---
"""


def test_parse_sections_extracts_three_sections():
    sections = _parse_sections(SAMPLE_OUTPUT)
    assert "タイトル" in sections
    assert "使用キーワード" in sections
    assert "本文" in sections


def test_parse_blog_extracts_title():
    post = parse_blog(SAMPLE_OUTPUT)
    assert post.title == "蒲田駅西口で眉毛WAX♪理想の眉が手に入るサロン"


def test_parse_blog_extracts_keywords():
    post = parse_blog(SAMPLE_OUTPUT)
    assert "蒲田" in post.keywords
    assert "蒲田駅西口" in post.keywords
    assert "眉毛WAX" in post.keywords
    assert "アイブロウ" in post.keywords


def test_parse_blog_body_includes_signature_and_headers():
    post = parse_blog(SAMPLE_OUTPUT)
    assert "【1】はじめに" in post.body
    assert "KATEstageLASH" in post.body
    assert "蒲田西口店" in post.body


def test_parse_blog_handles_missing_dashes():
    text = (
        "◆タイトル：\n"
        "テストタイトル\n"
        "\n"
        "◆使用キーワード：\n"
        "蒲田、眉毛\n"
        "\n"
        "◆本文：\n"
        "本文の内容です。\n"
    )
    post = parse_blog(text)
    assert post.title == "テストタイトル"
    assert post.keywords == ["蒲田", "眉毛"]
    assert "本文の内容です。" in post.body


def test_parse_blog_falls_back_to_raw_when_no_structure():
    text = "構造化されていない記事です。\nKATEstageLASH"
    post = parse_blog(text)
    assert post.title == ""
    assert post.keywords == []
    assert "構造化" in post.body


def test_parse_keywords_dedups_and_splits():
    assert _parse_keywords("蒲田、蒲田駅西口、眉毛") == ["蒲田", "蒲田駅西口", "眉毛"]
    assert _parse_keywords("a, a, b") == ["a", "b"]


def test_parse_keywords_handles_empty():
    assert _parse_keywords("") == []
    assert _parse_keywords("   ") == []


def test_generate_blog_with_mock(monkeypatch, tmp_path):
    prompt_path = tmp_path / "blog_prompt.md"
    prompt_path.write_text("テンプレ: {{TODAY_THEME}}", encoding="utf-8")
    monkeypatch.setattr("src.blog_writer.BLOG_PROMPT_PATH", prompt_path)

    mock_msg = SimpleNamespace(content=[SimpleNamespace(text=SAMPLE_OUTPUT)])
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_msg

    post = generate_blog("梅雨の眉ケア", client=mock_client)
    assert isinstance(post, BlogPost)
    assert post.title == "蒲田駅西口で眉毛WAX♪理想の眉が手に入るサロン"
    assert "蒲田" in post.keywords
    assert "【1】はじめに" in post.body

    # Verify the system prompt got the theme injected
    call_kwargs = mock_client.messages.create.call_args.kwargs
    assert "梅雨の眉ケア" in call_kwargs["system"]


def test_generate_blog_rejects_empty_theme():
    with pytest.raises(ValueError):
        generate_blog("")
    with pytest.raises(ValueError):
        generate_blog("   ")


def test_ensure_signature_appends_when_missing():
    body = "本文です。\n素敵な眉に整えましょう。"
    out = ensure_signature(body)
    assert out.endswith(SALON_SIGNATURE)
    assert "本文です。" in out
    assert "◆住所〒144-0051" in out
    assert "#蒲田駅西口" in out


def test_ensure_signature_strips_partial_and_appends_full():
    """LLM-truncated signature (only header) should be stripped and replaced with full."""
    body = (
        "本文の内容。\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "KATEstageLASH(ケイトステージラッシュ) 蒲田西口店\n"
        "━━━━━━━━━━━━━━━━━━━━━━━"
    )
    out = ensure_signature(body)
    # Body content preserved
    assert "本文の内容。" in out
    # Full signature present
    assert "◆住所〒144-0051" in out
    assert "◆営業時間9:00~20:00" in out
    assert "#蒲田駅西口" in out
    # Only one set of address lines (no duplicate)
    assert out.count("◆住所〒144-0051") == 1
    assert out.count("KATEstageLASH(ケイトステージラッシュ) 蒲田西口店") == 1


def test_ensure_signature_idempotent_for_full_signature():
    body = "本文。\n\n" + SALON_SIGNATURE
    out = ensure_signature(body)
    # Address should appear exactly once
    assert out.count("◆住所〒144-0051") == 1
    assert out.count("KATEstageLASH(ケイトステージラッシュ) 蒲田西口店") == 1
    # Body still intact
    assert "本文。" in out


def test_ensure_signature_empty_body():
    out = ensure_signature("")
    assert SALON_SIGNATURE in out


def test_parse_blog_always_emits_full_signature():
    """End-to-end: parse_blog output must end with the canonical signature."""
    raw_with_partial = """◆タイトル：
タイトル例

◆使用キーワード：
蒲田、眉毛

◆本文：
本文の内容。

━━━━━━━━━━━━━━━━━━━━━━━
KATEstageLASH(ケイトステージラッシュ) 蒲田西口店
━━━━━━━━━━━━━━━━━━━━━━━
"""
    post = parse_blog(raw_with_partial)
    assert "◆住所〒144-0051" in post.body
    assert "#蒲田駅西口" in post.body
    assert post.body.count("◆住所〒144-0051") == 1


def test_generate_blog_raises_on_empty_response(monkeypatch, tmp_path):
    prompt_path = tmp_path / "blog_prompt.md"
    prompt_path.write_text("テンプレ: {{TODAY_THEME}}", encoding="utf-8")
    monkeypatch.setattr("src.blog_writer.BLOG_PROMPT_PATH", prompt_path)

    mock_msg = SimpleNamespace(content=[SimpleNamespace(text="")])
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_msg

    with pytest.raises(RuntimeError, match="empty"):
        generate_blog("テーマ", client=mock_client)
