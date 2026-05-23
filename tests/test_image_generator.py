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


def test_build_image_prompt_universal_lash_style():
    """User requirement: 全画像で長め＋束感のまつげ。"""
    # Even for brow-focused menus, the universal lash style applies
    for menu in ("眉毛WAX", "眉毛スタイリング", "まつげパーマ", "ラッシュリフト"):
        prompt = build_image_prompt("テーマ", menu)
        assert "長め" in prompt, f"missing 長め for menu={menu}"
        assert "束感" in prompt, f"missing 束感 for menu={menu}"
        assert "クラスター" in prompt, f"missing クラスター for menu={menu}"


def test_build_image_prompt_universal_brow_style():
    """User requirement: 全画像で毛流れ整い・剃り跡なしの眉。"""
    for menu in ("眉毛WAX", "眉毛スタイリング", "まつげパーマ", "ラッシュリフト"):
        prompt = build_image_prompt("テーマ", menu)
        assert "毛流れ" in prompt, f"missing 毛流れ for menu={menu}"
        assert "剃" in prompt, f"missing 剃 (must mention no-razor-look) for menu={menu}"
        assert "自然な肌" in prompt, f"missing 自然な肌 for menu={menu}"


def test_build_image_prompt_lash_perm_extra_emphasis():
    """まつげパーマ の場合は基本に加えてさらに強調される。"""
    prompt = build_image_prompt("初夏のまつげケア", "まつげパーマ")
    # Has the universal base AND extra emphasis for lash perm
    assert "束感" in prompt
    assert "さらに強調" in prompt


def test_build_image_prompt_lash_lift_maintains_base_cluster():
    """Lash lift now keeps base cluster (no longer reduces it)."""
    prompt = build_image_prompt("ラッシュリフト紹介", "ラッシュリフト")
    assert "リフト" in prompt
    # Should NOT downgrade the cluster — universal style still applies
    assert "束感は控えめ" not in prompt
    assert "基本通り維持" in prompt


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
