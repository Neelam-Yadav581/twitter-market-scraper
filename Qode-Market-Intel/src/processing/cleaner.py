"""Cleaning/normalization pass turning raw scraped records into storage-ready dicts."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from src.utils.text_utils import clean_tweet_text, normalize_unicode

logger = logging.getLogger(__name__)

REQUIRED_FIELDS = ("tweet_id", "username", "content")


def clean_record(raw: dict) -> dict | None:
    """Returns a cleaned copy of `raw`, or None if the record is unusable."""
    if not all(raw.get(f) for f in REQUIRED_FIELDS):
        logger.debug("Dropping record missing required fields: %s", raw.get("tweet_id"))
        return None

    content = clean_tweet_text(raw["content"])
    if len(content) < 3:
        return None

    username = normalize_unicode(raw["username"]).lstrip("@").strip()
    timestamp = raw.get("timestamp") or datetime.now(timezone.utc).isoformat()

    try:
        reply = int(raw.get("reply_count", 0) or 0)
        retweet = int(raw.get("retweet_count", 0) or 0)
        like = int(raw.get("like_count", 0) or 0)
        view = int(raw.get("view_count", 0) or 0)
    except (TypeError, ValueError):
        logger.debug("Non-numeric engagement counts for %s; defaulting to 0", raw.get("tweet_id"))
        reply = retweet = like = view = 0

    return {
        "tweet_id": raw["tweet_id"],
        "username": username,
        "timestamp": timestamp,
        "content": content,
        "hashtags": raw.get("hashtags", []) or [],
        "mentions": raw.get("mentions", []) or [],
        "reply_count": reply,
        "retweet_count": retweet,
        "like_count": like,
        "view_count": view,
        "query_tag": raw.get("query_tag", ""),
        "engagement_score": reply + 2 * retweet + like,
    }


def clean_batch(raw_records: list[dict]) -> list[dict]:
    cleaned = [clean_record(r) for r in raw_records]
    cleaned = [c for c in cleaned if c is not None]
    logger.info("Cleaned %d/%d records", len(cleaned), len(raw_records))
    return cleaned
