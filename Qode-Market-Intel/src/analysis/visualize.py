"""Memory-efficient visualization for potentially large tweet datasets.

Rather than loading the full dataset into memory to plot, we (a) aggregate
first and plot the aggregate (bar/line charts), and (b) use reservoir
sampling to cap the number of raw points drawn on distribution plots
regardless of dataset size - both keep peak memory bounded and independent
of total row count, which matters for the "10x more data" scalability
requirement.
"""
from __future__ import annotations

import random
from pathlib import Path
from typing import Iterable

import matplotlib

matplotlib.use("Agg")  # headless-safe backend, no GUI/event-loop memory overhead
import matplotlib.pyplot as plt
import pandas as pd


def reservoir_sample(iterator: Iterable, k: int) -> list:
    """Reservoir sampling: a uniform sample of size k from a stream of
    unknown length in O(k) memory and a single O(n) pass - no need to
    materialize the full stream to sample from it.
    """
    sample: list = []
    for i, item in enumerate(iterator):
        if i < k:
            sample.append(item)
        else:
            j = random.randint(0, i)
            if j < k:
                sample[j] = item
    return sample


def plot_signal_by_hashtag(signal_df: pd.DataFrame, out_path: str | Path) -> Path | None:
    if signal_df.empty:
        return None

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.bar(signal_df["query_tag"], signal_df["composite_sentiment"], color="#1f77b4")
    ax.errorbar(
        signal_df["query_tag"],
        signal_df["composite_sentiment"],
        yerr=[
            (signal_df["composite_sentiment"] - signal_df["confidence_low"]).clip(lower=0),
            (signal_df["confidence_high"] - signal_df["composite_sentiment"]).clip(lower=0),
        ],
        fmt="none",
        ecolor="black",
        capsize=4,
    )
    ax.axhline(0, color="gray", linewidth=0.8)
    ax.set_ylabel("Composite sentiment signal")
    ax.set_title("Composite trading signal by hashtag (with confidence interval)")
    plt.xticks(rotation=20)
    fig.tight_layout()

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    return out_path


def plot_engagement_distribution(
    batches: Iterable[pd.DataFrame], out_path: str | Path, sample_size: int = 5000
) -> Path | None:
    """Streams batches (e.g. from ParquetStore.iter_batches) and plots a
    sampled engagement histogram without holding the full dataset in memory.
    """

    def _row_stream():
        for batch_df in batches:
            for value in batch_df["engagement_score"]:
                yield value

    sampled = reservoir_sample(_row_stream(), sample_size)
    if not sampled:
        return None

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.hist(sampled, bins=40, color="#ff7f0e", edgecolor="black", alpha=0.8)
    ax.set_xlabel("Engagement score (replies + 2*retweets + likes)")
    ax.set_ylabel("Tweet count (sampled)")
    ax.set_title(f"Engagement distribution (reservoir sample, n={len(sampled)})")
    fig.tight_layout()

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    return out_path


def plot_volume_timeseries(df: pd.DataFrame, out_path: str | Path, freq: str = "1h") -> Path | None:
    if df.empty:
        return None

    series = (
        df.set_index(pd.to_datetime(df["timestamp"], utc=True, errors="coerce"))
        .resample(freq)
        .size()
    )
    fig, ax = plt.subplots(figsize=(9, 4))
    ax.plot(series.index, series.values, color="#2ca02c")
    ax.set_ylabel("Tweets per interval")
    ax.set_title(f"Tweet volume over time (resampled {freq})")
    fig.autofmt_xdate()
    fig.tight_layout()

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    return out_path
