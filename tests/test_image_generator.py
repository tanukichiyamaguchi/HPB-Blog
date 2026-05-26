"""Tests for src/image_generator.

The current prompt is a fixed salon-owner-authored specification, so the
prompt-shape tests just verify the key requirements (Before/After, 4:3, no
nose, etc.) appear verbatim.
"""
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


# ---- prompt content ----


def test_prompt_specifies_japanese_woman_persona():
    prompt = build_image_prompt("どんなテーマでも", "どんなメニューでも")
    assert "日本人女性" in prompt
    assert "20代後半〜30代前半" in prompt


def test_prompt_is_before_after_layout():
    prompt = build_image_prompt("t", "m")
    # User explicitly asked for a Before/After layout
    assert "Before After" in prompt or "Before After構成" in prompt
    assert "上下2分割" in prompt
    assert "上段は施術前" in prompt
    assert "下段は施術後" in prompt


def test_prompt_specifies_brow_wax_and_lash_perm_in_after():
    prompt = build_image_prompt("t", "m")
    # Bottom panel showcases both signature menus
    assert "眉毛WAX後" in prompt
    assert "まつ毛パーマ後" in prompt or "まつげパーマ後" in prompt


def test_prompt_excludes_full_face():
    prompt = build_image_prompt("t", "m")
    assert "顔全体は写さず" in prompt
    assert "眉毛と目元のみ" in prompt
    assert "片目だけのクローズアップ" in prompt


def test_prompt_specifies_4_3_aspect_ratio():
    prompt = build_image_prompt("t", "m")
    assert "4:3" in prompt
    assert "横長" in prompt


def test_prompt_calls_out_realistic_salon_case_photo_style():
    prompt = build_image_prompt("t", "m")
    assert "サロン症例写真" in prompt
    # Avoids ad-shoot polish
    assert "美肌加工" in prompt  # negation: "過度に美肌加工しない"
    assert "広告感" in prompt    # negation: "広告感よりも...サロン症例写真"


def test_prompt_is_static_across_themes_and_menus():
    """Salon owner wants a consistent look — params are intentionally ignored."""
    p1 = build_image_prompt("夏のキャンペーン", "眉毛WAX")
    p2 = build_image_prompt("冬の保湿ケア", "まつげパーマ")
    p3 = build_image_prompt("", "")
    assert p1 == p2 == p3


def test_prompt_ignores_rng_parameter():
    import random
    p1 = build_image_prompt("t", "m", rng=random.Random(1))
    p2 = build_image_prompt("t", "m", rng=random.Random(999))
    assert p1 == p2


def test_prompt_is_non_empty():
    prompt = build_image_prompt("t", "m")
    assert len(prompt) > 100


# ---- extension resolver ----


@pytest.mark.parametrize("mime,expected", [
    ("image/png", ".png"),
    ("image/jpeg", ".jpg"),
    ("image/jpg", ".jpg"),
    ("image/webp", ".webp"),
    ("image/gif", ".png"),  # unknown → fallback to png
    ("", ".png"),
    (None, ".png"),
])
def test_resolve_extension(mime, expected):
    assert _resolve_extension(mime) == expected


# ---- response extractor ----


def test_extract_image_returns_data_and_mime():
    inline = SimpleNamespace(data=b"PNGBYTES", mime_type="image/png")
    part = SimpleNamespace(inline_data=inline)
    content = SimpleNamespace(parts=[part])
    candidate = SimpleNamespace(content=content)
    response = SimpleNamespace(candidates=[candidate])

    data, mime = _extract_image(response)
    assert data == b"PNGBYTES"
    assert mime == "image/png"


def test_extract_image_skips_non_inline_parts():
    """First part has no inline_data; we should find the next part."""
    text_part = SimpleNamespace(inline_data=None)
    inline = SimpleNamespace(data=b"BYTES", mime_type="image/jpeg")
    image_part = SimpleNamespace(inline_data=inline)
    content = SimpleNamespace(parts=[text_part, image_part])
    candidate = SimpleNamespace(content=content)
    response = SimpleNamespace(candidates=[candidate])

    data, mime = _extract_image(response)
    assert data == b"BYTES"
    assert mime == "image/jpeg"


def test_extract_image_defaults_to_png_when_mime_missing():
    inline = SimpleNamespace(data=b"X", mime_type="")
    part = SimpleNamespace(inline_data=inline)
    content = SimpleNamespace(parts=[part])
    candidate = SimpleNamespace(content=content)
    response = SimpleNamespace(candidates=[candidate])

    _, mime = _extract_image(response)
    assert mime == "image/png"


def test_extract_image_raises_when_no_inline_data():
    response = SimpleNamespace(candidates=[])
    with pytest.raises(RuntimeError, match="No image data"):
        _extract_image(response)


def test_extract_image_raises_when_empty_parts():
    content = SimpleNamespace(parts=[])
    candidate = SimpleNamespace(content=content)
    response = SimpleNamespace(candidates=[candidate])
    with pytest.raises(RuntimeError, match="No image data"):
        _extract_image(response)


# ---- end-to-end (mocked client) ----


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
    # New behaviour: prompt is the fixed salon prompt, not parameterised
    assert "サロン症例写真" in result.prompt
    assert "4:3" in result.prompt
