"""Generate a featured image via Gemini, avoiding before/after wording."""
from __future__ import annotations

import logging
import os
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
    "まつげは長めで密度があり、毛先がランダムに集まって自然な束感（クラスター感・たばかん）"
    "を作る立体的なカール。隙間が美しく、毛束が自然にまとまった仕上がり"
)
_BASE_BROW_STYLE = (
    "眉は毛流れがきれいに整い、1本1本が同じ方向に揃ったナチュラルブロウ。"
    "眉周りは剃った跡やワックス跡、肌の赤みや凹凸が一切見えない自然な肌質。"
    "産毛は丁寧に処理されつつ、剃毛感のないなめらかで自然な肌の表情"
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


_FORBIDDEN_WORDS = ("ビフォーアフター", "ビフォー", "アフター", "術前", "術後", "before", "after")


def build_image_prompt(theme: str, menu_focus: str) -> str:
    """Build a natural-language image prompt for Gemini image generation.

    Composition requirements (all mandatory):
      - Persona: Japanese woman in her late 20s
      - Frontal view (両目が正面、首/肩のひねりなし)
      - Tight crop on EYES + EYEBROWS only — no nose, mouth, hair, forehead, ears
      - White / off-white solid background
      - Photorealistic style

    Universal beauty defaults (every image, regardless of menu_focus):
      - Lashes: long, dense, with natural bunched/clustered curl
      - Brows: clean flow, no shaved/waxed look, smooth natural skin
    """
    menu_hint = _MENU_VISUAL_HINTS.get(menu_focus, "ナチュラルで美しい目元のサロンスタイル")
    prompt = (
        "ペルソナ：20代後半（27〜29歳）の日本人女性。\n"
        "構図：顔を正面に向けたクローズアップ、両目と眉だけが画面中央にタイトに収まる。\n"
        "厳格なトリミング指示：眉の少し上から目の下のごく一部までだけを写す。"
        "鼻・口・頬・顎・耳・髪・額・首・肩は一切フレーム内に入れないこと。"
        "横顔・斜めアングル・俯瞰・あおりは禁止、視線はカメラ正面。\n"
        "【基本の仕上がり（全画像共通・厳守）】\n"
        f"- まつげ：{_BASE_LASH_STYLE}。\n"
        f"- 眉：{_BASE_BROW_STYLE}。\n"
        f"【今回のメニュー強調】{menu_hint}。\n"
        f"テーマ：{theme}。\n"
        "ライティング：柔らかな自然光、明るく清潔感のあるサロン撮影。\n"
        "背景：白〜オフホワイトの単色無地、シンプルでミニマル。\n"
        "メイク：控えめでナチュラル、肌は素肌感のある美しい質感。\n"
        "スタイル：写真リアル、高解像度、フォトグラフィック、サロンスタイルの参考イメージ。"
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
