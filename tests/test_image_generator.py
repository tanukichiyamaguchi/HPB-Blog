from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.image_generator import (
    _extract_image,
    _resolve_extension,
    build_image_prompt,
    generate_image,
)


def test_build_image_prompt_includes_theme_and_menu_hint():
    prompt = build_image_prompt("梅雨の眉ケア", "眉毛WAX")
    assert "梅雨の眉ケア" in prompt
    assert "眉" in prompt
    assert "日本人女性" in prompt


def test_build_image_prompt_handles_unknown_menu():
    prompt = build_image_prompt("テスト", "未知のメニュー")
    assert "テスト" in prompt
    assert "日本人女性" in prompt


def test_build_image_prompt_specifies_late_20s_persona():
    """User requirement: 20代後半女性のペルソナ。"""
    prompt = build_image_prompt("テーマ", "眉毛WAX")
    assert "20代後半" in prompt


def test_build_image_prompt_specifies_frontal_view():
    """User requirement: 正面からの画像。"""
    prompt = build_image_prompt("テーマ", "眉毛WAX")
    assert "正面" in prompt
    # Forbidden angle words appear only inside negative-instruction sentences
    assert "横顔・斜めアングル・俯瞰・あおりは禁止" in prompt


def test_build_image_prompt_excludes_nose_hair_etc():
    """User requirement: 鼻や髪は写さず、目元のみにトリミング。"""
    prompt = build_image_prompt("テーマ", "眉毛WAX")
    # The negative-instruction sentence must list every excluded body part
    for forbidden_part in ("鼻", "口", "頬", "耳", "髪", "額", "首"):
        assert forbidden_part in prompt, f"missing exclusion for {forbidden_part}"
    assert "フレーム内に入れない" in prompt


def test_build_image_prompt_lash_perm_emphasizes_cluster():
    """User requirement: まつげパーマは束感を意識。"""
    prompt = build_image_prompt("初夏のまつげケア", "まつげパーマ")
    assert "束感" in prompt
    assert "クラスター" in prompt


def test_build_image_prompt_lash_lift_avoids_cluster_emphasis():
    """Lash lift differs from perm: natural lift, no cluster emphasis."""
    prompt = build_image_prompt("ラッシュリフト紹介", "ラッシュリフト")
    assert "リフト" in prompt
    # Lash-lift hint should not promote 束感 (only lash-perm does)
    assert "束感は控えめ" in prompt


def test_build_image_prompt_rejects_forbidden_words():
    # Building a prompt with forbidden words shouldn't happen via the public API,
    # but the validation should catch it if someone modifies _MENU_VISUAL_HINTS.
    # We test by passing a theme with forbidden wording.
    with pytest.raises(ValueError, match="forbidden"):
        build_image_prompt("ビフォーアフター比較", "眉毛WAX")
    with pytest.raises(ValueError, match="forbidden"):
        build_image_prompt("術前と術後の違い", "眉毛WAX")


def test_resolve_extension_png():
    assert _resolve_extension("image/png") == ".png"


def test_resolve_extension_jpeg():
    assert _resolve_extension("image/jpeg") == ".jpg"
    assert _resolve_extension("image/jpg") == ".jpg"


def test_resolve_extension_webp():
    assert _resolve_extension("image/webp") == ".webp"


def test_resolve_extension_default():
    assert _resolve_extension("") == ".png"
    assert _resolve_extension("unknown") == ".png"


def test_extract_image_returns_bytes_and_mime():
    inline = SimpleNamespace(data=b"\x89PNG_FAKE", mime_type="image/png")
    part = SimpleNamespace(inline_data=inline)
    content = SimpleNamespace(parts=[part])
    candidate = SimpleNamespace(content=content)
    response = SimpleNamespace(candidates=[candidate])

    data, mime = _extract_image(response)
    assert data == b"\x89PNG_FAKE"
    assert mime == "image/png"


def test_extract_image_skips_non_image_parts():
    text_part = SimpleNamespace(inline_data=None, text="some text")
    inline = SimpleNamespace(data=b"\x89PNG_FAKE", mime_type="image/png")
    image_part = SimpleNamespace(inline_data=inline)
    content = SimpleNamespace(parts=[text_part, image_part])
    candidate = SimpleNamespace(content=content)
    response = SimpleNamespace(candidates=[candidate])

    data, mime = _extract_image(response)
    assert data == b"\x89PNG_FAKE"


def test_extract_image_raises_when_no_image():
    text_part = SimpleNamespace(inline_data=None, text="no image here")
    content = SimpleNamespace(parts=[text_part])
    candidate = SimpleNamespace(content=content)
    response = SimpleNamespace(candidates=[candidate])

    with pytest.raises(RuntimeError, match="No image data"):
        _extract_image(response)


def test_generate_image_writes_file(tmp_path: Path):
    inline = SimpleNamespace(data=b"\x89PNG\x0d\x0a\x1a\x0aFAKE", mime_type="image/png")
    part = SimpleNamespace(inline_data=inline)
    content = SimpleNamespace(parts=[part])
    candidate = SimpleNamespace(content=content)
    response = SimpleNamespace(candidates=[candidate])

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = response

    out_base = tmp_path / "img"
    result = generate_image("テーマX", "眉毛WAX", out_base, client=mock_client)

    assert result.path.exists()
    assert result.path.suffix == ".png"
    assert result.mime_type == "image/png"
    assert result.path.read_bytes() == b"\x89PNG\x0d\x0a\x1a\x0aFAKE"
    assert "テーマX" in result.prompt
