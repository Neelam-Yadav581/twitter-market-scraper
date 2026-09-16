"""Text cleaning and extraction helpers for tweet content."""
from __future__ import annotations

import re
import unicodedata

URL_RE = re.compile(r"https?://\S+|www\.\S+")
HASHTAG_RE = re.compile(r"#(\w+)", re.UNICODE)
MENTION_RE = re.compile(r"@(\w+)", re.UNICODE)
_ZERO_WIDTH_CHARS = "".join(chr(cp) for cp in (0x200B, 0x200C, 0x200D, 0x200E, 0x200F, 0xFEFF))
ZERO_WIDTH_RE = re.compile(f"[{_ZERO_WIDTH_CHARS}]")
WHITESPACE_RE = re.compile(r"\s+")

_SUFFIX_MULTIPLIERS = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}


def normalize_unicode(text: str) -> str:
    """NFC-normalize text so Devanagari and other Indic scripts compare/hash consistently."""
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text)
    return ZERO_WIDTH_RE.sub("", text)


def clean_tweet_text(text: str) -> str:
    """Strip URLs and collapse whitespace while preserving hashtags/mentions/Indic text."""
    text = normalize_unicode(text or "")
    text = URL_RE.sub("", text)
    text = WHITESPACE_RE.sub(" ", text).strip()
    return text


def extract_hashtags(text: str) -> list[str]:
    return [f"#{tag.lower()}" for tag in HASHTAG_RE.findall(text or "")]


def extract_mentions(text: str) -> list[str]:
    return [f"@{name}" for name in MENTION_RE.findall(text or "")]


def parse_engagement_count(raw: str | int | float | None) -> int:
    """Parse counters like '1.2K', '3,401', '2M' into an int. Returns 0 on anything unparsable."""
    if raw is None:
        return 0
    if isinstance(raw, (int, float)):
        return int(raw)
    raw = raw.strip().replace(",", "")
    if not raw:
        return 0
    match = re.match(r"^([\d.]+)\s*([KkMmBb]?)$", raw)
    if not match:
        digits = re.sub(r"[^\d]", "", raw)
        return int(digits) if digits else 0
    value, suffix = match.groups()
    multiplier = _SUFFIX_MULTIPLIERS.get(suffix.lower(), 1)
    try:
        return int(float(value) * multiplier)
    except ValueError:
        return 0
