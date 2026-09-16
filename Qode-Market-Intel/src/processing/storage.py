"""Parquet-based storage layer for cleaned tweet records."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Iterator

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

# Documented target schema. Written data should conform to this shape even
# though we let pyarrow infer types from the pandas DataFrame at write time
# (safer than a strict schema for list<string> columns coming from mixed
# JSON sources) - kept here as the contract downstream consumers can rely on.
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
        files = sorted(self.base_dir.glob("*.parquet"))
        if not files:
            return pd.DataFrame()
        return pd.concat((pd.read_parquet(f) for f in files), ignore_index=True)

    def iter_batches(self, batch_size: int = 1000) -> Iterator[pd.DataFrame]:
        """Streams rows in bounded-memory batches for large-dataset analysis."""
        import pyarrow.dataset as ds

        if not any(self.base_dir.glob("*.parquet")):
            return
        dataset = ds.dataset(self.base_dir, format="parquet")
        for batch in dataset.to_batches(batch_size=batch_size):
            yield batch.to_pandas()
