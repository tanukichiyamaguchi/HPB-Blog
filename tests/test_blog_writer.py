from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.blog_writer import (
    BlogPost,
    _parse_keywords,
    _parse_sections,
    enforce_body_length,
    enforce_title_length,
    ensure_signature,
    generate_blog,
    parse_blog,
    strip_emoji,
    strip_partial_signature,
)
from src.config import MAX_BODY_LENGTH, MAX_TITLE_LENGTH, SALON_SIGNATURE


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


def test_strip_emoji_removes_supplementary_plane_emojis():
    text = "蒲田で眉毛WAX🎉素敵な仕上がり✨💕"
    out = strip_emoji(text)
    assert "🎉" not in out
    assert "✨" not in out
    assert "💕" not in out
    assert "蒲田で眉毛WAX素敵な仕上がり" in out


def test_strip_emoji_removes_dingbats():
    text = "梅雨対策✂✈✅にぴったり"
    out = strip_emoji(text)
    assert "✂" not in out
    assert "✈" not in out
    assert "✅" not in out
    assert "梅雨対策にぴったり" in out


def test_strip_emoji_preserves_allowed_symbols():
    """User-allowed symbols ♪ ＊ * ^ ◯ ◎ must survive emoji stripping."""
    text = "蒲田駅西口♪眉毛WAXで美眉◎すっきり◯人気＊おすすめ*^^"
    out = strip_emoji(text)
    assert "♪" in out
    assert "＊" in out
    assert "*" in out
    assert "◯" in out
    assert "◎" in out
    assert "^^" in out
    assert out == text  # nothing should have been removed


def test_strip_emoji_preserves_japanese_punctuation():
    text = "「梅雨」の眉対策、〜整え方〜。！？"
    out = strip_emoji(text)
    assert out == text


def test_enforce_title_length_no_change_when_short():
    title = "蒲田駅西口♪眉毛WAX"
    assert enforce_title_length(title) == title


def test_enforce_title_length_truncates_to_max():
    long_title = "梅雨前に整えておきたい！蒲田駅西口で眉毛WAXして崩れ知らずの美眉へ"
    out = enforce_title_length(long_title)
    assert len(out) <= MAX_TITLE_LENGTH


def test_enforce_title_length_cuts_at_natural_boundary():
    """If there's a ♪ or ！ past the midpoint, prefer cutting there over mid-word."""
    title = "蒲田駅西口で眉毛WAX♪崩れ知らずの美眉へ整える"
    out = enforce_title_length(title, max_len=20)
    # ♪ at index 12 is past half (10), so we should cut there
    assert out.endswith("♪")
    assert len(out) <= 20


def test_enforce_title_length_hard_cuts_when_no_separator():
    title = "あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほ"
    out = enforce_title_length(title, max_len=10)
    assert len(out) <= 10


def test_parse_blog_strips_emoji_and_truncates_title():
    raw = """◆タイトル：
🎉梅雨前に整えておきたい！蒲田駅西口で眉毛WAXして崩れ知らずの美眉へ✨💕

◆使用キーワード：
蒲田、眉毛

◆本文：
本文の内容です。✨気持ちいいですよね♪
"""
    post = parse_blog(raw)
    # Emoji removed
    assert "🎉" not in post.title
    assert "✨" not in post.title
    assert "💕" not in post.title
    assert "✨" not in post.body
    # ♪ preserved in body
    assert "♪" in post.body
    # Title length enforced
    assert len(post.title) <= MAX_TITLE_LENGTH


def test_parse_blog_keeps_short_clean_title_intact():
    raw = """◆タイトル：
蒲田駅西口♪眉毛WAXで美眉

◆使用キーワード：
蒲田

◆本文：
本文。
"""
    post = parse_blog(raw)
    assert post.title == "蒲田駅西口♪眉毛WAXで美眉"


def test_strip_partial_signature_removes_partial():
    body = (
        "本文。\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "KATEstageLASH(ケイトステージラッシュ) 蒲田西口店\n"
    )
    out = strip_partial_signature(body)
    assert out == "本文。"
    assert "━━━" not in out
    assert "KATEstageLASH" not in out


def test_strip_partial_signature_no_op_when_clean():
    body = "純粋な本文だけ。"
    assert strip_partial_signature(body) == "純粋な本文だけ。"


def test_enforce_body_length_no_change_when_short():
    short = "本文" * 50  # 100 chars
    out = enforce_body_length(short)
    assert out == short


def test_enforce_body_length_truncates_long_content():
    long_content = "とても長い本文の内容です。" * 100  # ~1300 chars
    out = enforce_body_length(long_content)
    # After truncation, content + signature must fit ≤ MAX_BODY_LENGTH
    # so content itself must be ≤ MAX_BODY_LENGTH - signature - separator
    from src.blog_writer import _SIGNATURE_RESERVED_CHARS

    assert len(out) <= MAX_BODY_LENGTH - _SIGNATURE_RESERVED_CHARS


def test_enforce_body_length_prefers_sentence_boundary():
    content = "一つ目の文。" * 20 + "二つ目。" * 100  # 120 + 400 chars
    out = enforce_body_length(content)
    # Should not end mid-word; expect to end with 。 or 、 or 一文区切り
    assert out.rstrip().endswith(("。", "、", "！", "♪")) or len(out) == 0


def test_parse_blog_total_length_within_1000():
    long_body = "とても長い本文の内容です。" * 100  # ~1300 chars
    raw = f"""◆タイトル：
タイトル

◆使用キーワード：
蒲田

◆本文：
{long_body}
"""
    post = parse_blog(raw)
    assert len(post.body) <= MAX_BODY_LENGTH, (
        f"Body is {len(post.body)} chars; must be ≤ {MAX_BODY_LENGTH}"
    )


def test_parse_blog_preserves_signature_when_truncating():
    long_body = "本文の内容。" * 200
    raw = f"""◆タイトル：
タイトル

◆使用キーワード：
蒲田

◆本文：
{long_body}
"""
    post = parse_blog(raw)
    # Total bounded
    assert len(post.body) <= MAX_BODY_LENGTH
    # Signature still intact (not truncated)
    assert "◆住所〒144-0051" in post.body
    assert "◆営業時間9:00~20:00" in post.body
    assert "#蒲田駅西口" in post.body
    assert post.body.endswith(SALON_SIGNATURE)


def test_parse_blog_short_content_unchanged():
    short_raw = """◆タイトル：
短いタイトル

◆使用キーワード：
蒲田

◆本文：
短い本文の内容です。
"""
    post = parse_blog(short_raw)
    assert "短い本文の内容です。" in post.body
    assert len(post.body) <= MAX_BODY_LENGTH


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
