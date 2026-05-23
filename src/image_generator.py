"""Generate a featured image via Gemini, avoiding before/after wording."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types

from src.config import GEMINI_IMAGE_MODEL
from src.utils import retry

log = logging.getLogger(__name__)


@dataclass
class ImageResult:
    path: Path
    mime_type: str
    prompt: str


_MENU_VISUAL_HINTS: dict[str, str] = {
    "眉毛WAX": (
        "産毛まで丁寧に整えられたナチュラルな眉、自然なアーチ、"
        "毛流れが整い濃すぎない上品な仕上がり"
    ),
    "眉毛スタイリング": (
        "毛流れが整えられた上品なナチュラルブロウ、骨格に沿った自然なライン、"
        "アイブロウメイクを引き立てるベース"
    ),
    "まつげパーマ": (
        "上向きに美しくカールしたまつげ、毛先がランダムに集まって"
        "束感（クラスター感・たばかん）が出たナチュラルで立体的な仕上がり、"
        "目元がぱっちりと際立つ自然な印象"
    ),
    "ラッシュリフト": (
        "根元から自然に立ち上がったまつげ、リフトアップ効果で目元が明るく開いた印象、"
        "束感は控えめで毛が均一に上向きの自然なリフト"
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
    """
    menu_hint = _MENU_VISUAL_HINTS.get(menu_focus, "ナチュラルで美しい目元のサロンスタイル")
    prompt = (
        "ペルソナ：20代後半（27〜29歳）の日本人女性。\n"
        "構図：顔を正面に向けたクローズアップ、両目と眉だけが画面中央にタイトに収まる。\n"
        "厳格なトリミング指示：眉の少し上から目の下のごく一部までだけを写す。"
        "鼻・口・頬・顎・耳・髪・額・首・肩は一切フレーム内に入れないこと。"
        "横顔・斜めアングル・俯瞰・あおりは禁止、視線はカメラ正面。\n"
        f"施術スタイルの仕上がり：{menu_hint}。\n"
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

    api_client = client or genai.Client(api_key=_require_api_key())

    def _call() -> Any:
        return api_client.models.generate_content(
            model=GEMINI_IMAGE_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE", "TEXT"],
            ),
        )

    response = retry(_call, max_attempts=3, base_delay=2.0, logger=log)
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
