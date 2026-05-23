from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.blog_writer import (
    BlogPost,
    _parse_keywords,
    _parse_sections,
    generate_blog,
    parse_blog,
)


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


def test_generate_blog_raises_on_empty_response(monkeypatch, tmp_path):
    prompt_path = tmp_path / "blog_prompt.md"
    prompt_path.write_text("テンプレ: {{TODAY_THEME}}", encoding="utf-8")
    monkeypatch.setattr("src.blog_writer.BLOG_PROMPT_PATH", prompt_path)

    mock_msg = SimpleNamespace(content=[SimpleNamespace(text="")])
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_msg

    with pytest.raises(RuntimeError, match="empty"):
        generate_blog("テーマ", client=mock_client)
