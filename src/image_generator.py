"""Generate a featured image via Gemini, avoiding before/after wording."""
from __future__ import annotations

import logging
import os
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types

from src.config import (
    API_RETRY_ATTEMPTS,
    API_RETRY_BASE_DELAY_SEC,
    GEMINI_IMAGE_MODEL,
    GEMINI_TIMEOUT_SEC,
)
from src.utils import retry

log = logging.getLogger(__name__)


@dataclass
class ImageResult:
    path: Path
    mime_type: str
    prompt: str


# Universal beauty defaults — applied to every image regardless of menu_focus.
# These represent the salon's standard finishing quality.
_BASE_LASH_STYLE = (
    "まつげは長めで密度があり、上向きに大きくカールし、毛が整然と並んだ美しい束感"
    "（クラスター感・たばかん）を作る立体的な仕上がり。毛先がきちんと集まって"
    "規則的な束を作り、毛流れが上向きに揃っていて、バラバラに散らばった毛は一切なし。"
    "毛と毛の間隔も整って、美容雑誌のアイメイクページのような美しいビジュアル"
)
_BASE_BROW_STYLE = (
    "眉は毛流れがきれいに整い、1本1本が同じ方向に揃ったナチュラルブロウ。"
    "眉周りの肌は陶器のように滑らかで均一、黒い毛穴・黒い点・毛根の黒ずみ・"
    "剃り跡・ワックス跡・肌の赤みや凹凸が一切見えない美しい肌質。"
    "産毛は丁寧に処理されつつ、剃毛感や毛穴の暗い影が見えない、"
    "なめらかで自然な肌の表情"
)


_MENU_VISUAL_HINTS: dict[str, str] = {
    "眉毛WAX": (
        "今回のメニューは眉毛WAX。眉のラインと毛流れの美しさが特に際立つ仕上がり、"
        "骨格に合った上品なアーチ"
    ),
    "眉毛スタイリング": (
        "今回のメニューは眉毛スタイリング。骨格に沿ったライン取りと毛流れの整いが"
        "特に映える仕上がり"
    ),
    "まつげパーマ": (
        "今回のメニューはまつげパーマ。基本の束感をさらに強調し、上向きに大きく"
        "カールしたまつげで目元がぱっちりと際立つ立体的な仕上がり"
    ),
    "ラッシュリフト": (
        "今回のメニューはラッシュリフト。根元から自然に立ち上がった上向きの"
        "リフトアップ効果で、目元が明るく開いた印象（束感は基本通り維持）"
    ),
}


# Diversity pools — sampled per-call so each generation depicts a different woman.
_EYE_VARIATIONS: tuple[str, ...] = (
    "アーモンド型の落ち着いた目元",
    "丸みのある大きな目元",
    "切れ長で上品な目元",
    "ぱっちりとした幅広二重の目元",
    "末広二重の柔らかな目元",
    "奥二重でクールな印象の目元",
    "一重で凛とした印象の目元",
    "たれ目気味で柔らかい印象の目元",
    "つり目気味でシャープな印象の目元",
    "やや離れ目の優しい印象の目元",
)

_SKIN_VARIATIONS: tuple[str, ...] = (
    "色白でなめらかな肌",
    "標準的な健康的な肌色",
    "やや小麦色の自然な肌",
    "陶器のような明るい肌",
    "ほのかにそばかすのある自然な肌",
    "頬にうっすら血色のある柔らかな肌",
)

_BROW_DENSITY_VARIATIONS: tuple[str, ...] = (
    "やや細めで上品なナチュラル眉",
    "標準的な太さの自然な眉",
    "やや太めでしっかりとしたナチュラル眉",
)


# "Skilled friend with an iPhone Pro" aesthetic — high-quality but natural,
# not studio-staged. The previous "professional studio lighting" wording made
# images look like commercial ad shots; users want something that looks like
# a great Instagram post from a salon, taken casually but well.
_IPHONE_PHOTO_STYLE = (
    "プロカメラマン並みの腕を持つ友人が iPhone（最新 Pro モデル）で気軽に"
    "撮影したような、自然で親しみやすい高画質写真。"
    "自然光またはやわらかい屋内光（窓際の柔らかい光、または天井のディフューズ"
    "された照明）の下で、目元と眉が明るく程よくクリアに見える。"
    "瞳には自然な光の反射（catch light）が小さく映り込み、肌は明るく整って"
    "見えるが、美肌アプリのような完全に平面化した不自然さは避け、毛穴・微細な"
    "質感・自然な肌のニュアンスは適度に残す。"
    "iPhone Pro の computational photography 由来の、暗部の自然な持ち上げ、"
    "発色の良さ、シャープすぎない自然な解像感。"
    "まつげ1本1本と眉の毛流れまで視認できる接写品質を保ちつつ、"
    "スタジオの硬い均一ライティングや雑誌広告のような完璧すぎる仕上がりではなく、"
    "「サロンの SNS や Instagram で見かけるような素敵な日常写真」のリアルさ"
)


_FORBIDDEN_WORDS = ("ビフォーアフター", "ビフォー", "アフター", "術前", "術後", "before", "after")


def build_image_prompt(
    theme: str,
    menu_focus: str,
    *,
    rng: random.Random | None = None,
) -> str:
    """Build a natural-language image prompt for Gemini image generation.

    Composition requirements (all mandatory):
      - Persona: Japanese woman in her late 20s
      - Frontal view (両目が正面、首/肩のひねりなし)
      - Tight crop on EYES + EYEBROWS only — no nose, mouth, hair, forehead, ears
      - White / off-white solid background

    Universal beauty defaults (every image, regardless of menu_focus):
      - Lashes: long, dense, with natural bunched/clustered curl
      - Brows: clean flow, no shaved/waxed look, smooth natural skin

    Diversity: eye type / skin / brow density are sampled randomly per call so
    consecutive generations depict different women.

    Photo style: high-quality iPhone Pro shot by a skilled photographer
    (natural light, not studio-staged).

    Pass ``rng`` (a ``random.Random``) for deterministic sampling in tests.
    """
    r = rng if rng is not None else random
    eye_type = r.choice(_EYE_VARIATIONS)
    skin_type = r.choice(_SKIN_VARIATIONS)
    brow_base = r.choice(_BROW_DENSITY_VARIATIONS)

    menu_hint = _MENU_VISUAL_HINTS.get(menu_focus, "ナチュラルで美しい目元のサロンスタイル")
    prompt = (
        "ペルソナ：20代後半（27〜29歳）の日本人女性。\n"
        f"目元タイプ：{eye_type}（毎回異なる個性の女性として描画する）。\n"
        f"肌タイプ：{skin_type}。\n"
        "【画像レイアウト・必須】1枚の画像内に、上下に2つの目元クローズアップが並ぶ"
        "縦長の2分割コラージュ。上パネルと下パネルが横並びの帯状に積み重なった構成。\n"
        "- 上パネル：目元の超クローズアップ（片目を中心、または両目とごく一部の眉）\n"
        "- 下パネル：同じ女性の目元クローズアップ（やや異なる距離感・アングルで、別カット風）\n"
        "- 両パネルとも同一の女性、同一の仕上がり（まつげの束感／眉／ライティングを統一）\n"
        "- 両パネル間は細い白い区切り線または直接接した境界、画像全体の比率は縦長（3:4 または 4:5）\n"
        "- ※施術の変化を見せる対比演出ではなく、雑誌の同一シーンを2カットで魅せる構成\n"
        "構図（各パネル共通）：まつげ1本1本や眉の毛流れまでクリアに視認できる超拡大率。\n"
        "厳格なトリミング指示：眉の少し上から目の下のごく一部までだけを写す。"
        "鼻・口・頬・顎・耳・髪・額・首・肩は一切フレーム内に入れないこと。"
        "横顔・斜めアングル・俯瞰・あおりは禁止、視線はカメラ正面。\n"
        "【基本の仕上がり（全画像共通・厳守）】\n"
        f"- まつげ：{_BASE_LASH_STYLE}。\n"
        f"- 眉：{_BASE_BROW_STYLE}（眉の毛量ベース：{brow_base}）。\n"
        f"【今回のメニュー強調】{menu_hint}。\n"
        f"テーマ：{theme}。\n"
        f"撮影スタイル：{_IPHONE_PHOTO_STYLE}。\n"
        "背景：白〜オフホワイトの無地でシンプル、被写体（目元）を引き立てる清潔な背景。\n"
        "メイク：控えめでナチュラル、肌は素肌感のある美しい質感（過度な美肌フィルターは"
        "避けるが、サロンで仕上げた整った美しさは表現）。"
    )
    lowered = prompt.lower()
    for w in _FORBIDDEN_WORDS:
        if w.lower() in lowered:
            raise ValueError(f"Image prompt contains forbidden wording: {w!r}")
    return prompt


def _resolve_extension(mime_type: str) -> str:
    mt = (mime_type or "").lower()
    if "png" in mt:
        return ".png"
    if "jpeg" in mt or "jpg" in mt:
        return ".jpg"
    if "webp" in mt:
        return ".webp"
    return ".png"


def _extract_image(response: Any) -> tuple[bytes, str]:
    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            inline = getattr(part, "inline_data", None)
            if inline is not None:
                data = getattr(inline, "data", None)
                mime = getattr(inline, "mime_type", "") or "image/png"
                if data:
                    return data, mime
    raise RuntimeError("No image data returned from Gemini API")


def generate_image(
    theme: str,
    menu_focus: str,
    out_path_base: Path,
    client: genai.Client | None = None,
) -> ImageResult:
    """Generate one image and write it to disk. Returns the final path with extension."""
    prompt = build_image_prompt(theme, menu_focus)
    log.info("Generating image (theme=%s, menu=%s)", theme, menu_focus)

    api_client = client or genai.Client(
        api_key=_require_api_key(),
        http_options=types.HttpOptions(timeout=int(GEMINI_TIMEOUT_SEC * 1000)),
    )

    def _call() -> Any:
        log.info("Calling Gemini model=%s (timeout=%ss)", GEMINI_IMAGE_MODEL, GEMINI_TIMEOUT_SEC)
        return api_client.models.generate_content(
            model=GEMINI_IMAGE_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE", "TEXT"],
            ),
        )

    response = retry(
        _call,
        max_attempts=API_RETRY_ATTEMPTS,
        base_delay=API_RETRY_BASE_DELAY_SEC,
        logger=log,
    )
    image_bytes, mime_type = _extract_image(response)

    out_path = out_path_base.with_suffix(_resolve_extension(mime_type))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(image_bytes)
    log.info("Saved image: %s (%d bytes, %s)", out_path, len(image_bytes), mime_type)

    return ImageResult(path=out_path, mime_type=mime_type, prompt=prompt)


def _require_api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY environment variable is not set")
    return key
