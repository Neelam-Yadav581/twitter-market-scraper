"""Parquet-based storage layer for cleaned tweet records."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Iterator

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

SCHEMA_DESCRIPTION = {
    "tweet_id": "string",
    "username": "string",
    "timestamp": "timestamp[us, tz=UTC]",
    "content": "string",
    "hashtags": "list<string>",
    "mentions": "list<string>",
    "reply_count": "int32",
    "retweet_count": "int32",
    "like_count": "int32",
    "view_count": "int32",
    "query_tag": "string",
    "engagement_score": "int32",
    "collected_date": "date32",
}


class ParquetStore:
    """Writes tweet records partitioned by collection date.

    Partitioning by date keeps individual files small and lets downstream
    analysis (or a future Spark/Dask/Polars job) prune irrelevant partitions
    instead of scanning the whole dataset - the main lever for the "10x more
    data" scalability requirement without changing the storage format.
    """

    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def write(self, records: Iterable[dict], run_id: str) -> Path:
        df = pd.DataFrame(list(records))
        if df.empty:
            raise ValueError("No records to write")

        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        fallback_date = pd.Timestamp.now(tz="UTC").date()
        df["collected_date"] = df["timestamp"].dt.date
        df["collected_date"] = df["collected_date"].fillna(fallback_date)

        table = pa.Table.from_pandas(df, preserve_index=False)
        out_path = self.base_dir / f"tweets_{run_id}.parquet"
        pq.write_table(table, out_path, compression="snappy")
        return out_path

    def read_all(self) -> pd.DataFrame:
        """Reads and concatenates every parquet file in this store's
        directory - i.e. the full accumulated history of everything ever
        written here, not just one run's output. Deliberately cumulative
        (useful for analyzing many past scrapes together), but NOT what a
        single run's own report should use: two runs over overlapping or
        duplicate source data (e.g. the same tweets written under two
        different run_ids) silently double-counts here. A per-run report
        should read its own `write()` return path directly instead - see
        `run_pipeline.py`.
        """
        files = sorted(self.base_dir.glob("*.parquet"))
        if not files:
            return pd.DataFrame()
        return pd.concat((pd.read_parquet(f) for f in files), ignore_index=True)

    def iter_batches(self, batch_size: int = 1000, path: str | Path | None = None) -> Iterator[pd.DataFrame]:
        """Streams rows in bounded-memory batches. Pass `path` to stream just
        one parquet file (a single run's own output); omit it to stream
        every file in this store's directory (see `read_all()`'s docstring
        for why that's cumulative-history behavior, not a single run's).
        """
        import pyarrow.dataset as ds

        if path is not None:
            if not Path(path).exists():
                return
            dataset = ds.dataset(str(path), format="parquet")
        else:
            if not any(self.base_dir.glob("*.parquet")):
                return
            dataset = ds.dataset(self.base_dir, format="parquet")
        for batch in dataset.to_batches(batch_size=batch_size):
            yield batch.to_pandas()
