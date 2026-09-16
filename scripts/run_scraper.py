"""CLI entry point to run the live X (Twitter) scraper.

Run this locally (not in a headless CI/sandbox), against a Chrome profile
already logged into X manually beforehand - see README.md "Login setup" for
the one-time setup steps. Set that profile's path as `scraper.user_data_dir`
in config.yaml. This script never reads or submits credentials itself: an
earlier version did (X_USERNAME/X_PASSWORD via .env), but automating the
login submission was the single riskiest action for tripping X's
anti-automation detection, and it did exactly that during development (see
docs/TECHNICAL_APPROACH.md). Reusing an already-authenticated profile
removes that step entirely.

Usage - one go (single run covering all configured hashtags/target count):
    python scripts/run_scraper.py --out data/raw/raw_scrape.json

Usage - batches (shorter runs, spaced out in time - lower detection risk
than one long session, and results merge cleanly at analysis time since
scripts/run_pipeline.py dedupes across files):
    python scripts/run_scraper.py --tags "#nifty50"   --limit 500 --out data/raw/batch1.json
    python scripts/run_scraper.py --tags "#sensex"    --limit 500 --out data/raw/batch2.json
    python scripts/run_scraper.py --tags "#intraday"  --limit 500 --out data/raw/batch3.json
    python scripts/run_scraper.py --tags "#banknifty" --limit 500 --out data/raw/batch4.json
    # then: python scripts/run_pipeline.py --input "data/raw/batch*.json"

Legal note: X's Terms of Service prohibit automated scraping. This tool is
for personal/educational research only. Use your own account, keep request
rates human-like (already rate-limited below), and do not resell or
redistribute collected data.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml

from src.scraper.twitter_scraper import TwitterScraper
from src.utils.logging_config import setup_logger

logger = setup_logger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape X search results for market hashtags")
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--out", default="data/raw/raw_scrape.json")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Total tweet target across all hashtags, overriding config.yaml's "
        "scraper.target_tweets. Use a small number (e.g. 2) for a quick smoke "
        "test before committing to a full run.",
    )
    parser.add_argument(
        "--tags",
        type=str,
        default=None,
        help="Comma-separated subset of hashtags to scrape (e.g. '#nifty50'), "
        "overriding config.yaml's scraper.hashtags. Useful to test a single "
        "hashtag quickly, or to run one hashtag per batch.",
    )
    args = parser.parse_args()

    with open(args.config, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    if not config["scraper"].get("user_data_dir"):
        raise SystemExit(
            "config.yaml's scraper.user_data_dir is not set. See README.md 'Login "
            "setup' for the one-time steps to create and log into a dedicated "
            "Chrome profile, then point user_data_dir at it."
        )

    hashtags = [t.strip() for t in args.tags.split(",")] if args.tags else config["scraper"]["hashtags"]
    target_total = args.limit if args.limit is not None else config["scraper"]["target_tweets"]
    per_tag_target = max(1, target_total // len(hashtags))

    scraper = TwitterScraper(config)
    all_records: list[dict] = []
    try:
        scraper.start()
        if not scraper.is_logged_in():
            raise SystemExit(
                "The profile at scraper.user_data_dir isn't logged into X. Log in "
                "manually in that profile first (see README.md 'Login setup'), then re-run."
            )
        logger.info("Reusing already-authenticated persistent Chrome profile")

        for tag in hashtags:
            logger.info("Scraping %s (target=%d)", tag, per_tag_target)
            try:
                for record in scraper.search_hashtag(tag, per_tag_target):
                    all_records.append(record.__dict__)
            except Exception:
                logger.exception("Error scraping %s; continuing with remaining hashtags", tag)
    finally:
        scraper.stop()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(all_records, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Saved %d raw records -> %s", len(all_records), out_path)


if __name__ == "__main__":
    main()
