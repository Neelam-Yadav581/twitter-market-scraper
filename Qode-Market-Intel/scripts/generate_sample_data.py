"""Generates a synthetic sample dataset that mimics real scraped tweets.

Used to (a) produce the "sample output data" deliverable without requiring
live X credentials in an automated/sandboxed environment, and (b) exercise
the full clean -> dedup -> store -> analyze -> visualize pipeline end-to-end
so it can be verified independent of X's availability/DOM/anti-bot state.

For a live run against real data, use scripts/run_scraper.py locally with
your own X account instead.
"""
from __future__ import annotations

import argparse
import json
import random
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

HASHTAGS = ["#nifty50", "#sensex", "#intraday", "#banknifty"]

INDEX_NAMES = {"#nifty50": "Nifty", "#sensex": "Sensex", "#banknifty": "Banknifty", "#intraday": "Nifty"}

# Phrase *templates* rather than fixed phrases: {index}/{level}/{points}/{pct} are
# substituted with randomized values so 2000+ generated tweets have realistic
# content diversity instead of collapsing to a handful of literal duplicates.
BULLISH_TEMPLATES = [
    "{index} broke resistance at {level}, breakout confirmed, up {pct}% today",
    "{index} looking strong, bullish target of {level} hit, +{points} pts",
    "Upper circuit again on {index}, rally continues past {level}",
    "Buy on dips, support held well near {level} for {index}",
    "{index} rallies {points} points, momentum traders piling in",
]
BEARISH_TEMPLATES = [
    "{index} breakdown below support at {level}, down {pct}% today",
    "Bearish crossover on {index}, short covering expected near {level}",
    "Lower circuit hit on a few counters, panic selling drags {index} down {points} pts",
    "Stop loss hit near {level}, resistance too strong for {index}",
    "{index} slips {points} points as sentiment turns cautious",
]
NEUTRAL_TEMPLATES = [
    "{index} flat today around {level}, waiting for RBI cues",
    "Intraday range-bound session for {index}, low volatility near {level}",
    "Watching {index} for a breakout either side of {level}",
    "{index} trades in a tight {points}-point range today",
]
HINDI_TEMPLATES = [
    "आज {index} में तेज़ी है, {level} के पास कारोबार",
    "{index} में गिरावट देखी जा रही है, {points} अंक नीचे",
    "{index} में उतार-चढ़ाव जारी है, निवेशक सतर्क",
]
USERNAMES = [f"trader_{i}" for i in range(1, 250)]


def _random_level(query_tag: str) -> int:
    base = {"#nifty50": 22000, "#sensex": 72000, "#banknifty": 48000, "#intraday": 22000}[query_tag]
    return base + random.randint(-500, 500)


def _random_timestamp(hours_back: int = 24) -> str:
    now = datetime.now(timezone.utc)
    delta = timedelta(seconds=random.randint(0, hours_back * 3600))
    return (now - delta).isoformat()


def _make_tweet(force_duplicate_of: dict | None = None) -> dict:
    if force_duplicate_of:
        content = force_duplicate_of["content"]
        hashtags = force_duplicate_of["hashtags"]
        query_tag = force_duplicate_of["query_tag"]
    else:
        bucket = random.choices(
            ["bull", "bear", "neutral", "hindi"], weights=[0.35, 0.3, 0.2, 0.15]
        )[0]
        template = {
            "bull": random.choice(BULLISH_TEMPLATES),
            "bear": random.choice(BEARISH_TEMPLATES),
            "neutral": random.choice(NEUTRAL_TEMPLATES),
            "hindi": random.choice(HINDI_TEMPLATES),
        }[bucket]
        query_tag = random.choice(HASHTAGS)
        hashtags = list({query_tag, random.choice(HASHTAGS)})
        level = _random_level(query_tag)
        phrase = template.format(
            index=INDEX_NAMES[query_tag],
            level=level,
            points=random.randint(20, 650),
            pct=round(random.uniform(0.2, 3.5), 2),
        )
        content = f"{phrase} {' '.join(hashtags)}"

    username = random.choice(USERNAMES)
    return {
        "tweet_id": f"{username}:{uuid.uuid4()}",
        "username": username,
        "timestamp": _random_timestamp(),
        "content": content,
        "hashtags": hashtags,
        "mentions": [],
        "reply_count": random.randint(0, 50),
        "retweet_count": random.randint(0, 80),
        "like_count": random.randint(0, 500),
        "view_count": random.randint(100, 20000),
        "query_tag": query_tag,
    }


def generate(n: int, duplicate_ratio: float = 0.05) -> list[dict]:
    records: list[dict] = []
    for _ in range(n):
        if records and random.random() < duplicate_ratio:
            records.append(_make_tweet(force_duplicate_of=random.choice(records)))
        else:
            records.append(_make_tweet())
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic sample tweet data")
    parser.add_argument("--count", type=int, default=2200)
    parser.add_argument("--out", type=str, default="data/sample/raw_sample.json")
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    records = generate(args.count)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Generated {len(records)} synthetic records -> {out_path}")


if __name__ == "__main__":
    main()
