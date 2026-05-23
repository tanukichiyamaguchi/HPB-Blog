"""Generate the blog body via Claude using the structured prompt template."""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

from anthropic import Anthropic

from src.config import (
    ANTHROPIC_TIMEOUT_SEC,
    API_RETRY_ATTEMPTS,
    API_RETRY_BASE_DELAY_SEC,
    BLOG_PROMPT_PATH,
    CLAUDE_MAX_TOKENS_BLOG,
    CLAUDE_MODEL,
    MAX_BODY_LENGTH,
    MAX_TITLE_LENGTH,
    SALON_SIGNATURE,
    SIGNATURE_HORIZONTAL_RULE,
)
from src.utils import read_text, retry

log = logging.getLogger(__name__)


@dataclass
class BlogPost:
    title: str
    keywords: list[str] = field(default_factory=list)
    body: str = ""
    raw: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "keywords": self.keywords,
            "body": self.body,
        }


_SECTION_HEADER_RE = re.compile(r"^◆\s*([^:：]+?)\s*[:：]?\s*$")
_FENCE_RE = re.compile(r"^-{3,}\s*$")


def _parse_sections(raw: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current_key: str | None = None
    for line in raw.splitlines():
        stripped = line.strip()
        if _FENCE_RE.match(stripped):
            continue
        m = _SECTION_HEADER_RE.match(stripped)
        if m:
            current_key = m.group(1).strip()
            sections.setdefault(current_key, [])
            continue
        if current_key is not None:
            sections[current_key].append(line)
    return {k: "\n".join(v).strip() for k, v in sections.items()}


def _normalize_title(text: str) -> str:
    cleaned = text.strip()
    # Strip surrounding markdown emphasis or quotes
    cleaned = re.sub(r"^[\*\#\s「『\"']+", "", cleaned)
    cleaned = re.sub(r"[\*\#\s」』\"']+$", "", cleaned)
    return cleaned.strip()


def _parse_keywords(text: str) -> list[str]:
    if not text:
        return []
    # Split on Japanese/Western commas and newlines
    parts = re.split(r"[、,，\n]+", text)
    out: list[str] = []
    seen: set[str] = set()
    for p in parts:
        token = p.strip(" \t-•・()（）「」『』\"'*")
        if not token:
            continue
        if token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


# Emoji ranges to strip. Covers the Supplementary Multilingual Plane (most modern
# emoji like 🎉✨💕😊⭐🌸) and the Dingbats block (✂✈✅✨). Intentionally does NOT
# strip BMP "Miscellaneous Symbols" so allowed symbols are preserved:
#   ♪ (U+266A), ＊ (U+FF0A), ◯ (U+25EF), ◎ (U+25CE), ★/☆ (U+2605/U+2606).
_EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FFFF"  # Supplementary plane symbols / emoji
    "\U00002700-\U000027BF"  # Dingbats
    "]+"
)


def strip_emoji(text: str) -> str:
    """Remove emoji glyphs. HPB-allowed symbols (♪ ＊ ^ ◯ ◎) are preserved."""
    return _EMOJI_RE.sub("", text)


# Punctuation we'd rather cut on than mid-word, in priority order.
_TITLE_CUT_PRIORITY: tuple[str, ...] = ("♪", "！", "!", "。", "、", "・", " ", "　")


def enforce_title_length(title: str, max_len: int = MAX_TITLE_LENGTH) -> str:
    """Ensure title length ≤ max_len, cutting at a natural boundary when possible."""
    if len(title) <= max_len:
        return title
    candidate = title[:max_len]
    # Try to truncate at a separator past the halfway mark, so we don't return ""
    half = max(1, max_len // 2)
    for sep in _TITLE_CUT_PRIORITY:
        idx = candidate.rfind(sep)
        if idx >= half:
            return candidate[: idx + 1].rstrip()
    return candidate.rstrip()


_SIGNATURE_SEPARATOR = "\n\n"
# Char budget reserved for the canonical signature (separator + signature itself).
_SIGNATURE_RESERVED_CHARS = len(_SIGNATURE_SEPARATOR) + len(SALON_SIGNATURE)
# Char budget left for the LLM-generated body content.
MAX_BODY_CONTENT_LENGTH = max(0, MAX_BODY_LENGTH - _SIGNATURE_RESERVED_CHARS)
# HPB form caps newlines in 本文 at 80. The canonical signature contributes
# a fixed count; we measure dynamically.
MAX_BODY_NEWLINES = 80
_SIGNATURE_NEWLINES = SALON_SIGNATURE.count("\n") + _SIGNATURE_SEPARATOR.count("\n")
MAX_CONTENT_NEWLINES = max(0, MAX_BODY_NEWLINES - _SIGNATURE_NEWLINES)


def strip_partial_signature(body: str) -> str:
    """Drop anything from the first horizontal-rule line onward.

    LLMs frequently emit only the header part of the signature; this normalizes
    the body so we can re-append the canonical signature without duplication.
    """
    body = body.rstrip()
    lines = body.splitlines()
    for i, line in enumerate(lines):
        if SIGNATURE_HORIZONTAL_RULE in line:
            return "\n".join(lines[:i]).rstrip()
    return body


def enforce_newline_limit(content: str, max_newlines: int = MAX_CONTENT_NEWLINES) -> str:
    """Collapse newlines so the content has ≤ max_newlines newlines.

    HPB form caps 本文 at 改行80回以下. The canonical signature contributes a fixed
    number; this leaves a budget for the LLM-written portion.

    Three-step de-escalation, each preserving as much structure as possible:
      1. Collapse 3+ consecutive newlines to 2 (kills only extra paragraph breaks).
      2. Collapse 2+ consecutive newlines to 1 (removes paragraph breaks entirely).
      3. Past-budget single newlines → replaced with spaces (joins lines).
    """
    import re

    if content.count("\n") <= max_newlines:
        return content
    # Step 1
    collapsed = re.sub(r"\n{3,}", "\n\n", content)
    if collapsed.count("\n") <= max_newlines:
        return collapsed
    # Step 2
    collapsed = re.sub(r"\n{2,}", "\n", collapsed)
    if collapsed.count("\n") <= max_newlines:
        return collapsed
    # Step 3: hard cap by replacing surplus newlines with spaces.
    if max_newlines <= 0:
        return collapsed.replace("\n", " ")
    parts = collapsed.split("\n")
    # Keep the first (max_newlines + 1) parts separated by newlines, then join
    # any remaining parts with single spaces (so no content is lost).
    head = "\n".join(parts[: max_newlines + 1])
    tail_parts = [p for p in parts[max_newlines + 1:] if p]
    if not tail_parts:
        return head
    return head + " " + " ".join(tail_parts)


def enforce_body_length(content: str, max_total: int = MAX_BODY_LENGTH) -> str:
    """Truncate body content so the eventual ``content + signature`` ≤ ``max_total``.

    Pass the body WITHOUT the signature; the function deducts the signature's
    reserved char budget and cuts at the most natural boundary that fits.
    """
    budget = max(0, max_total - _SIGNATURE_RESERVED_CHARS)
    if len(content) <= budget:
        return content
    candidate = content[:budget]
    # Prefer paragraph-level boundaries, then sentence, then comma.
    for sep in ("\n\n", "。\n", "！\n", "♪\n", "。", "！", "♪", "\n", "、"):
        idx = candidate.rfind(sep)
        if idx >= budget // 2:
            return candidate[: idx + len(sep)].rstrip()
    return candidate.rstrip()


def ensure_signature(body: str) -> str:
    """Strip any partial signature the LLM may have produced and append the canonical one.

    The LLM sometimes truncates the signature (only the header line and store name,
    missing address/hours/hashtags). To guarantee a complete signature, we cut the body
    at the first horizontal-rule line and append the full canonical signature.
    """
    stripped = strip_partial_signature(body)
    return stripped + _SIGNATURE_SEPARATOR + SALON_SIGNATURE


def parse_blog(raw: str) -> BlogPost:
    """Robustly extract title/keywords/body from the structured Claude output.

    Applies post-processing defenses regardless of LLM compliance:
      - emoji stripping (HPB displays emoji as garbled text)
      - title length enforcement (max 25 chars per HPB UI cap)
      - canonical signature appended at body tail
    """
    raw = raw.strip()
    sections = _parse_sections(raw)
    title = _normalize_title(sections.get("タイトル", ""))
    keywords = _parse_keywords(sections.get("使用キーワード", ""))
    body = sections.get("本文", "").strip()

    if not body:
        # Fallback: if structure is missing, treat everything after the last header as body
        body = raw

    # Defensive cleanup: strip emoji from both title and body
    title_stripped = strip_emoji(title)
    if title_stripped != title:
        log.warning("Stripped emoji from title: %r → %r", title, title_stripped)
    title = title_stripped

    body_stripped = strip_emoji(body)
    if body_stripped != body:
        log.warning("Stripped emoji from body (%d chars removed)", len(body) - len(body_stripped))
    body = body_stripped

    # Enforce title length (HPB caps at 25 chars)
    if len(title) > MAX_TITLE_LENGTH:
        log.warning(
            "Title length %d exceeds %d; truncating: %r",
            len(title), MAX_TITLE_LENGTH, title,
        )
        title = enforce_title_length(title)

    # Strip any partial signature first so we measure only the LLM-written content,
    # then enforce body length so (content + canonical signature) ≤ MAX_BODY_LENGTH.
    body_content = strip_partial_signature(body)
    if len(body_content) > MAX_BODY_CONTENT_LENGTH:
        original_len = len(body_content)
        body_content = enforce_body_length(body_content)
        log.warning(
            "Body content %d > %d chars; truncated to %d (signature %d will be appended → total %d ≤ %d)",
            original_len, MAX_BODY_CONTENT_LENGTH, len(body_content),
            _SIGNATURE_RESERVED_CHARS, len(body_content) + _SIGNATURE_RESERVED_CHARS, MAX_BODY_LENGTH,
        )
    if body_content.count("\n") > MAX_CONTENT_NEWLINES:
        original_nl = body_content.count("\n")
        body_content = enforce_newline_limit(body_content)
        log.warning(
            "Body newlines %d > %d; collapsed to %d (signature adds %d → total %d ≤ %d)",
            original_nl, MAX_CONTENT_NEWLINES, body_content.count("\n"),
            _SIGNATURE_NEWLINES, body_content.count("\n") + _SIGNATURE_NEWLINES, MAX_BODY_NEWLINES,
        )
    # Final assembly via the canonical signature appender (single source of truth,
    # eliminates the prior dead-code dup with ensure_signature).
    body = ensure_signature(body_content)
    assert len(body) <= MAX_BODY_LENGTH, f"body length {len(body)} exceeds {MAX_BODY_LENGTH}"
    assert body.count("\n") <= MAX_BODY_NEWLINES, (
        f"body newlines {body.count(chr(10))} exceeds {MAX_BODY_NEWLINES}"
    )

    return BlogPost(title=title, keywords=keywords, body=body, raw=raw)


def _build_system_prompt(theme: str) -> str:
    template = read_text(BLOG_PROMPT_PATH)
    return template.replace("{{TODAY_THEME}}", theme)


def _extract_text(message: Any) -> str:
    parts: list[str] = []
    for block in getattr(message, "content", []) or []:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


def generate_blog(theme: str, client: Anthropic | None = None) -> BlogPost:
    if not theme or not theme.strip():
        raise ValueError("theme must be a non-empty string")

    system_prompt = _build_system_prompt(theme)
    user_prompt = (
        "上記の指示に従い、本日のブログ記事を出力形式の通り1本作成してください。"
    )
    log.info("Generating blog body (theme=%s)", theme)

    api_client = client or Anthropic(
        api_key=_require_api_key(),
        timeout=ANTHROPIC_TIMEOUT_SEC,
        max_retries=0,  # we handle retries ourselves
    )

    def _call() -> Any:
        return api_client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=CLAUDE_MAX_TOKENS_BLOG,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )

    message = retry(
        _call,
        max_attempts=API_RETRY_ATTEMPTS,
        base_delay=API_RETRY_BASE_DELAY_SEC,
        logger=log,
    )
    raw = _extract_text(message).strip()
    if not raw:
        raise RuntimeError("Claude returned empty blog content")

    post = parse_blog(raw)
    if not post.title:
        log.warning("Could not parse title from blog output; falling back to theme")
        post.title = theme
    if not post.body:
        raise RuntimeError("Could not parse blog body from Claude output")
    return post


def _require_api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY environment variable is not set")
    return key
