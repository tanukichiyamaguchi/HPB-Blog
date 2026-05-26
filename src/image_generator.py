"""Generate a featured image via Gemini for daily salon-blog posts."""
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


# Fixed image prompt provided by the salon owner — a 4:3 landscape before/after
# case-study collage of a Japanese woman's eye + brow area, top panel showing
# the pre-treatment state and bottom panel showing the post-treatment result
# (brow wax + lash perm). The ``theme`` / ``menu_focus`` / ``rng`` parameters
# are accepted for backwards compatibility with the cron pipeline but are
# intentionally unused — the salon wants a consistent showcase look across
# all posts regardless of that day's theme.
_FIXED_IMAGE_PROMPT = (
    "日本人女性の目元アップの美容サロン症例写真。20代後半〜30代前半の自然な"
    "日本人女性モデル。顔全体は写さず、眉毛と目元のみを横長に大きく写す。"
    "上下2分割のBefore After構成。\n"
    "\n"
    "上段は施術前。眉毛は少し産毛やばらつきがあり、眉下と眉尻に余分な毛が"
    "残っている。まつ毛は自然でやや下向き、控えめなカール。すっぴんに近い"
    "ナチュラルな肌、薄いまぶた、ブラウンの瞳、自然な涙袋。日本人らしい"
    "奥二重〜控えめな二重の目元。\n"
    "\n"
    "下段は施術後。眉毛WAX後で眉下ラインがすっきり整い、自然な平行アーチ眉。"
    "細すぎず、毛流れが一本一本見えるナチュラルな仕上がり。眉頭はふんわり、"
    "眉尻は細く整っている。まつ毛パーマ後で上まつ毛が根元から自然に立ち上がり、"
    "扇状にセパレートしている。過度な束感はなく、清潔感のある上品なカール。"
    "アイメイクはほぼなし、加工感の少ないリアルな肌質。\n"
    "\n"
    "美容サロンの施術事例写真のようなリアルフォト。明るい白色照明、清潔な"
    "室内、肌は明るく透明感があるが過度に美肌加工しない。毛穴や細かい産毛、"
    "まつ毛の細さが自然に見える。左右どちらか片目だけのクローズアップ。"
    "カメラは正面〜やや斜めから、目元にピントを合わせる。高解像度、"
    "スマートフォンで撮影したようなリアルな質感。広告感よりも実際のサロン"
    "症例写真に近い。\n"
    "\n"
    "アスペクト比は 4:3（横長）。"
)


def build_image_prompt(
    theme: str,
    menu_focus: str,
    *,
    rng: random.Random | None = None,
) -> str:
    """Return the salon's fixed showcase prompt.

    ``theme``, ``menu_focus``, and ``rng`` are accepted for compatibility with
    the existing pipeline but intentionally ignored — the salon owner wants
    a single consistent before/after showcase across all posts.
    """
    del theme, menu_focus, rng  # intentionally unused
    return _FIXED_IMAGE_PROMPT


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
