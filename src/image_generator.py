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
# not studio-staged. Modeled on salon SNS / Instagram posts: bright natural
# daylight (window light), iPhone Pro macro sharpness on lashes and brow flow,
# realistic skin texture (not airbrushed). Should look like a salon owner's
# casual but skilled phone shot of a client.
_IPHONE_PHOTO_STYLE = (
    "iPhone Pro（最新モデル）の超広角または接写マクロモードで撮影したような、"
    "自然光中心の明るい高画質写真。"
    "光源：午前〜午後の窓際の柔らかい自然光（直射ではなく、レースカーテン越し"
    "または天井のディフューズされた間接光）。光は被写体の正面〜やや斜め上から"
    "均一にあたり、瞳には大きめの楕円形 catch light（窓ガラスや照明の反射）が"
    "2〜3個自然に映り込む。"
    "解像感：iPhone Pro の computational photography 由来のシャープすぎない"
    "自然な解像で、まつげ1本1本・眉毛1本1本の毛流れまでクリアに視認できる"
    "が、線が立ちすぎてイラスト的にならない柔らかさ。"
    "肌の質感：陶器のように整っているが、毛穴・微細な凹凸・自然な肌のニュアンス"
    "は適度に残し、美肌アプリの完全に平面化したフィルター感は避ける。"
    "色味：ナチュラルでわずかに暖色寄り、肌色は健康的で日本人の標準的な色域。"
    "全体の雰囲気：プロのスタジオ撮影ではなく「サロンの公式 Instagram で"
    "見かけるような、丁寧に撮られた接写写真」。雑誌広告のような硬さや作り込み"
    "感は完全に排除"
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
        "\n"
        "【画像レイアウト・絶対厳守】1枚の画像内に、上下2つの目元接写写真が縦に"
        "積み重なる縦長コラージュ（比率は 4:3 または 3:2 の横長を縦に2枚重ねた"
        "全体縦長）。上パネルと下パネルは細い白い区切り線または直接接した境界で"
        "分かれる。両パネルとも同一の女性、同一の仕上がり（まつげ束感・眉・"
        "ライティング・色味すべて統一）で、わずかにアングル/距離が異なる別カット。\n"
        "- 上パネル：眉と目元のやや引き気味の接写（眉全体と目全体がフレームに収まる）\n"
        "- 下パネル：同じ女性の目元・眉のさらに寄った接写（まつげのカール感や眉毛"
        "1本1本がより細部まで見える）\n"
        "- ※変化を見せる対比ではなく、サロンが SNS に投稿する"
        "「丁寧に撮った2カット紹介」のイメージ\n"
        "\n"
        "【フレーミング・絶対厳守】各パネルとも以下を厳守：\n"
        "- 上端：眉のすぐ上（額の毛際が見えるか見えないかのライン）\n"
        "- 下端：下まつげのすぐ下〜目の下のごく一部のみ（涙袋まで）\n"
        "- ❌ **鼻（鼻先・小鼻・鼻筋）は絶対にフレーム内に入れない**\n"
        "- ❌ 口・唇・顎・頬の大部分は写さない\n"
        "- ✅ 顔の横に髪が少しだけ写り込むのは OK（自然な環境として）\n"
        "- ✅ 耳の一部や、顔横の指・道具がチラリと見えるのも OK\n"
        "- 視線：カメラ正面または斜め上を見るような自然な視線（厳密な真正面でなくてもよい）\n"
        "- 横顔・俯瞰・極端なあおりは禁止\n"
        "\n"
        "【基本の仕上がり（全画像共通・厳守）】\n"
        f"- まつげ：{_BASE_LASH_STYLE}。\n"
        f"- 眉：{_BASE_BROW_STYLE}（眉の毛量ベース：{brow_base}）。\n"
        f"【今回のメニュー強調】{menu_hint}。\n"
        f"テーマ：{theme}。\n"
        f"\n撮影スタイル：{_IPHONE_PHOTO_STYLE}。\n"
        "\n背景：オフホワイト〜薄いベージュの自然な背景（無地のシーツ・タオル・"
        "壁面など）。スタジオ撮影のような完全な真っ白背景ではなく、サロンの"
        "施術ベッド上で撮ったような柔らかい背景でも可。\n"
        "メイク：控えめでナチュラル、ノーメイク〜素肌風メイク。アイメイクは"
        "ほぼなし（まつげの自然な美しさを見せるため）。"
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
