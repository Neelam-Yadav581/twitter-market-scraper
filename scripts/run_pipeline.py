"""End-to-end pipeline: clean -> dedup -> store (Parquet) -> analyze -> visualize.

Usage:
    python scripts/run_pipeline.py --input data/raw/raw_scrape.json

    # Merge multiple scraper batches (e.g. several shorter runs instead of
    # one long one) into a single deduplicated analysis - comma-separated
    # paths and/or glob patterns are both supported:
    python scripts/run_pipeline.py --input "data/raw/batch1.json,data/raw/batch2.json"
    python scripts/run_pipeline.py --input "data/raw/batch*.json"
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import yaml

from src.analysis.aggregator import aggregate_signal
from src.analysis.signals import SignalExtractor
from src.analysis.visualize import (
    plot_engagement_distribution,
    plot_signal_by_hashtag,
    plot_volume_timeseries,
)
from src.processing.cleaner import clean_batch
from src.processing.deduplicator import Deduplicator
from src.processing.storage import ParquetStore
from src.utils.logging_config import setup_logger

logger = setup_logger(__name__)


def load_config(path: str = "config/config.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _resolve_input_paths(input_spec: str) -> list[Path]:
    """Expands a comma-separated list of paths and/or glob patterns (e.g.
    multiple scraper batch files) into a sorted, deduplicated file list.
    """
    paths: list[Path] = []
    for part in input_spec.split(","):
        part = part.strip()
        if not part:
            continue
        matches = glob.glob(part)
        if matches:
            paths.extend(Path(m) for m in matches)
        else:
            paths.append(Path(part))
    # dict.fromkeys: dedupes while preserving order, unlike set()
    return list(dict.fromkeys(paths))


def _load_raw_records(input_spec: str) -> tuple[list[dict], list[Path]]:
    paths = _resolve_input_paths(input_spec)
    if not paths:
        raise SystemExit(f"No input files matched: {input_spec!r}")

    all_records: list[dict] = []
    for path in paths:
        try:
            records = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.exception("Could not read/parse input file: %s", path)
            raise
        logger.info("Loaded %d raw records from %s", len(records), path)
        all_records.extend(records)
    return all_records, paths


def run(input_path: str, config_path: str = "config/config.yaml") -> None:
    config = load_config(config_path)

    raw_records, input_files = _load_raw_records(input_path)
    logger.info(
        "Loaded %d total raw records from %d file(s)", len(raw_records), len(input_files)
    )

    cleaned = clean_batch(raw_records)
    if not cleaned:
        logger.warning("No usable records after cleaning; aborting pipeline")
        return

    dedup_cfg = config["processing"]["bloom_filter"]
    deduplicator = Deduplicator(
        expected_items=dedup_cfg["capacity"],
        error_rate=dedup_cfg["error_rate"],
        near_dup_threshold=config["processing"]["near_duplicate_threshold"],
    )
    unique_records = [
        r for r in cleaned if not deduplicator.is_duplicate(r["username"], r["content"])
    ]
    logger.info(
        "Dedup: %d unique / %d cleaned (exact=%d, near=%d)",
        len(unique_records),
        len(cleaned),
        deduplicator.stats.exact_duplicates,
        deduplicator.stats.near_duplicates,
    )

    store = ParquetStore(config["storage"]["processed_dir"])
    if len(input_files) == 1:
        run_id = input_files[0].stem
    else:
        combined = "_".join(sorted(p.stem for p in input_files))
        digest = hashlib.sha1(combined.encode("utf-8")).hexdigest()[:8]
        run_id = f"batch_{len(input_files)}files_{digest}"
    parquet_path = store.write(unique_records, run_id=run_id)
    logger.info("Wrote %d records -> %s", len(unique_records), parquet_path)

    # Reads back only *this* run's own file, not store.read_all() - which
    # would silently pull in every other parquet file ever written to
    # processed_dir (past runs, including ones over overlapping/duplicate
    # source data) and inflate every downstream count and chart.
    df = pd.read_parquet(parquet_path)

    analysis_cfg = config["analysis"]
    extractor = SignalExtractor(
        bullish_terms=analysis_cfg["bullish_lexicon"],
        bearish_terms=analysis_cfg["bearish_lexicon"],
        max_features=analysis_cfg["tfidf"]["max_features"],
        ngram_range=tuple(analysis_cfg["tfidf"]["ngram_range"]),
        min_df=analysis_cfg["tfidf"]["min_df"],
    )
    featured_df = extractor.build_features(df)

    signal_df = aggregate_signal(
        featured_df,
        confidence=analysis_cfg["confidence_level"],
        bootstrap_samples=analysis_cfg["bootstrap_samples"],
    )

    reports_dir = Path("reports")
    reports_dir.mkdir(exist_ok=True)
    signal_df.to_csv(reports_dir / "composite_signals.csv", index=False)
    logger.info("Wrote signal report -> %s", reports_dir / "composite_signals.csv")

    plot_signal_by_hashtag(signal_df, reports_dir / "signal_by_hashtag.png")
    plot_volume_timeseries(featured_df, reports_dir / "volume_timeseries.png")
    plot_engagement_distribution(
        store.iter_batches(batch_size=500, path=parquet_path),
        reports_dir / "engagement_distribution.png",
    )
    logger.info("Wrote plots -> %s", reports_dir)

    print(signal_df.to_string(index=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the tweet analysis pipeline")
    parser.add_argument("--input", required=True, help="Path to raw JSON records")
    parser.add_argument("--config", default="config/config.yaml")
    args = parser.parse_args()
    run(args.input, args.config)
