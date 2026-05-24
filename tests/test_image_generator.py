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
    """User requirement: 正面〜やや上を見る自然な視線。"""
    prompt = build_image_prompt("テーマ", "眉毛WAX")
    assert "正面" in prompt
    assert "横顔" in prompt and "禁止" in prompt


def test_build_image_prompt_excludes_nose_strictly():
    """User requirement (latest): 鼻は絶対にフレーム内に入れない。"""
    prompt = build_image_prompt("テーマ", "眉毛WAX")
    # Nose exclusion is the strictest constraint — must be explicit
    assert "鼻" in prompt
    assert "絶対にフレーム内に入れない" in prompt
    # 口・顎 も除外対象
    assert "口" in prompt
    assert "顎" in prompt
    # 髪は逆に「少し写り込むのは OK」(reference images にも髪が見える)
    assert "髪" in prompt


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


def test_build_image_prompt_uses_skilled_iphone_aesthetic():
    """User requirement (latest): 写真撮影が上手な人が iPhone で撮影した品質。

    Pulls back from studio-grade staging while keeping macro-sharp closeup
    detail — should look like a great salon Instagram post, not a magazine ad.
    """
    prompt = build_image_prompt("テーマ", "眉毛WAX")
    # New aesthetic markers
    assert "iPhone" in prompt
    assert "自然" in prompt  # 自然光 / 自然な…
    # Studio-staged wording must be gone
    assert "モデル撮影用の均一なプロライティング" not in prompt
    assert "美容雑誌のアイメイク特集ページのような" not in prompt


def test_build_image_prompt_keeps_natural_skin_texture():
    """Professional but not over-retouched — natural skin nuance preserved."""
    prompt = build_image_prompt("テーマ", "眉毛WAX")
    # Should still avoid heavy face-tune
    assert "美肌アプリ" in prompt or "美肌フィルター" in prompt
    # Natural texture preserved
    assert "毛穴" in prompt
    assert "ニュアンス" in prompt or "質感" in prompt


def test_build_image_prompt_tight_eye_brow_crop():
    """User requirement: 眉のすぐ上〜下まつげのすぐ下まで、鼻は出さない。"""
    prompt = build_image_prompt("テーマ", "眉毛WAX")
    # Top boundary
    assert "眉のすぐ上" in prompt
    # Bottom boundary
    assert "下まつげのすぐ下" in prompt or "下まつげ" in prompt
    # Sharpness expectation
    assert "1本1本" in prompt


def test_build_image_prompt_two_panel_collage():
    """User requirement: 添付画像のように上下2分割の縦長コラージュ。"""
    prompt = build_image_prompt("テーマ", "眉毛WAX")
    # Layout markers
    assert "上パネル" in prompt
    assert "下パネル" in prompt
    assert "縦長" in prompt
    assert "コラージュ" in prompt
    # Must NOT be a before/after comparison
    assert "before" not in prompt.lower()
    assert "after" not in prompt.lower()
    assert "ビフォー" not in prompt
    assert "アフター" not in prompt


def test_build_image_prompt_no_visible_brow_pores():
    """User requirement: 眉周りの黒い毛穴は無いように。"""
    prompt = build_image_prompt("テーマ", "眉毛WAX")
    assert "黒い毛穴" in prompt
    assert "陶器のように" in prompt or "陶器" in prompt
    # The instruction must be a negation (no pores), not just "pores"
    assert "毛穴・黒い点" in prompt or "黒い毛穴・黒い点" in prompt


def test_build_image_prompt_neat_organized_lash_bundles():
    """User requirement: まつ毛をきれいな毛流れの束感に整える（バラバラはNG）。"""
    prompt = build_image_prompt("テーマ", "眉毛WAX")
    assert "整然と" in prompt
    assert "規則的な束" in prompt
    # Must reject the previous "random" cluster wording
    assert "ランダムに" not in prompt
    # Must explicitly forbid scattered hairs
    assert "バラバラに散らばった毛は一切なし" in prompt


def test_build_image_prompt_varies_by_seed():
    """Different seeds should yield different prompts (diversity)."""
    import random

    p1 = build_image_prompt("テーマ", "眉毛WAX", rng=random.Random(1))
    p2 = build_image_prompt("テーマ", "眉毛WAX", rng=random.Random(2))
    assert p1 != p2, "Same prompt across different seeds — diversity broken"


def test_build_image_prompt_eye_diversity_across_seeds():
    """Across many seeds, multiple distinct eye types should appear."""
    import random

    seen = set()
    for seed in range(50):
        prompt = build_image_prompt("テーマ", "眉毛WAX", rng=random.Random(seed))
        for line in prompt.splitlines():
            if line.startswith("目元タイプ"):
                seen.add(line.strip())
                break
    assert len(seen) >= 5, f"Only {len(seen)} unique eye types in 50 trials"


def test_build_image_prompt_includes_eye_and_skin_type_labels():
    """Each prompt should label the sampled eye and skin attributes."""
    prompt = build_image_prompt("テーマ", "眉毛WAX")
    assert "目元タイプ：" in prompt
    assert "肌タイプ：" in prompt
    assert "眉の毛量ベース：" in prompt


def test_build_image_prompt_same_seed_reproducible():
    """Same seed → same prompt (test determinism)."""
    import random

    p1 = build_image_prompt("テーマA", "眉毛WAX", rng=random.Random(42))
    p2 = build_image_prompt("テーマA", "眉毛WAX", rng=random.Random(42))
    assert p1 == p2


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
