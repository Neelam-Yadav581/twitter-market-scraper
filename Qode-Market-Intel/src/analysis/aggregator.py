"""Aggregate per-tweet features into a composite trading signal with confidence intervals."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _weighted_mean(values: np.ndarray, weights: np.ndarray) -> float:
    if len(values) == 0:
        return 0.0
    if weights.sum() == 0:
        return float(np.mean(values))
    return float(np.average(values, weights=weights))


def _bootstrap_ci(
    values: np.ndarray,
    weights: np.ndarray,
    confidence: float = 0.95,
    n_samples: int = 500,
    seed: int = 42,
) -> tuple[float, float]:
    """Bootstrap resampling for a distribution-free confidence interval - avoids
    assuming the lexicon-score distribution is normal (it's bounded in
    [-1, 1] and often spikes at 0 on quiet, low-signal days).
    """
    if len(values) == 0:
        return (0.0, 0.0)
    rng = np.random.default_rng(seed)
    n = len(values)
    means = np.empty(n_samples)
    for i in range(n_samples):
        idx = rng.integers(0, n, size=n)
        means[i] = _weighted_mean(values[idx], weights[idx])
    alpha = (1 - confidence) / 2
    return float(np.quantile(means, alpha)), float(np.quantile(means, 1 - alpha))


def aggregate_signal(
    df: pd.DataFrame,
    group_col: str = "query_tag",
    confidence: float = 0.95,
    bootstrap_samples: int = 500,
) -> pd.DataFrame:
    """Composite signal per hashtag: engagement-weighted lexicon sentiment,
    combined with tweet volume and average TF-IDF magnitude, with a
    bootstrap confidence interval on the sentiment component.
    """
    if df.empty:
        return pd.DataFrame(
            columns=[
                "query_tag",
                "tweet_volume",
                "avg_tfidf_norm",
                "composite_sentiment",
                "confidence_low",
                "confidence_high",
                "confidence_level",
                "total_engagement",
            ]
        )

    rows = []
    for tag, group in df.groupby(group_col):
        weights = (group["engagement_score"].to_numpy() + 1).astype(float)  # +1: never zero-weight
        sentiment = group["lexicon_score"].to_numpy(dtype=float)

        mean_sentiment = _weighted_mean(sentiment, weights)
        ci_low, ci_high = _bootstrap_ci(sentiment, weights, confidence, bootstrap_samples)

        rows.append(
            {
                "query_tag": tag,
                "tweet_volume": len(group),
                "avg_tfidf_norm": float(group["tfidf_norm"].mean()),
                "composite_sentiment": mean_sentiment,
                "confidence_low": ci_low,
                "confidence_high": ci_high,
                "confidence_level": confidence,
                "total_engagement": int(group["engagement_score"].sum()),
            }
        )
    return pd.DataFrame(rows).sort_values("tweet_volume", ascending=False).reset_index(drop=True)
