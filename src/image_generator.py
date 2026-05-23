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
    "眉毛WAX": "美しく整った眉のクローズアップ、ナチュラルなアーチ",
    "眉毛スタイリング": "上品で整った眉のスタイル参考、ナチュラルメイク",
    "まつげパーマ": "美しくカールしたまつげ、上向きの目元",
    "ラッシュリフト": "ナチュラルに立ち上がったまつげ、目元クローズアップ",
}


_FORBIDDEN_WORDS = ("ビフォーアフター", "ビフォー", "アフター", "術前", "術後", "before", "after")


def build_image_prompt(theme: str, menu_focus: str) -> str:
    """Build a natural-language image prompt. Forbids before/after wording."""
    menu_hint = _MENU_VISUAL_HINTS.get(menu_focus, "美しい目元のサロンスタイル参考")
    prompt = (
        "リアルな日本人女性の写真。"
        f"{menu_hint}。"
        f"テーマ：{theme}。"
        "目元周辺のクローズアップ、自然光で明るく清潔感のある雰囲気。"
        "背景は白系の単色で、シンプルでミニマル。"
        "メイクは控えめでナチュラル。サロンスタイルの参考イメージとして、"
        "高品質・高解像度・写真リアルな仕上がり。"
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
