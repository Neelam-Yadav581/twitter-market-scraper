"""Tests for TF-IDF + bullish/bearish lexicon feature extraction."""
import pandas as pd

from src.analysis.signals import SignalExtractor


def test_lexicon_score_bullish_and_bearish():
    extractor = SignalExtractor(
        bullish_terms=["breakout", "rally"], bearish_terms=["crash", "breakdown"]
    )
    assert extractor.lexicon_score("strong breakout and rally today") == 1.0
    assert extractor.lexicon_score("market crash and breakdown") == -1.0
    assert extractor.lexicon_score("flat session today") == 0.0


def test_build_features_adds_columns():
    extractor = SignalExtractor(bullish_terms=["breakout"], bearish_terms=["crash"])
    df = pd.DataFrame(
        {
            "content": [
                "nifty breakout confirmed",
                "sensex crash today",
                "flat session, nothing new",
            ]
        }
    )
    out = extractor.build_features(df)
    assert "tfidf_norm" in out.columns
    assert "lexicon_score" in out.columns
    assert len(out) == 3
