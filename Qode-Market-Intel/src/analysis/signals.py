"""Text-to-signal conversion: TF-IDF + custom finance-lexicon feature engineering."""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer

logger = logging.getLogger(__name__)


class SignalExtractor:
    def __init__(
        self,
        bullish_terms: list[str],
        bearish_terms: list[str],
        max_features: int = 5000,
        ngram_range: tuple[int, int] = (1, 2),
        min_df: int = 2,
    ):
        self.bullish_terms = [t.lower() for t in bullish_terms]
        self.bearish_terms = [t.lower() for t in bearish_terms]
        self.vectorizer = TfidfVectorizer(
            max_features=max_features,
            ngram_range=ngram_range,
            min_df=min_df,
            stop_words="english",
            # norm=None: TfidfVectorizer L2-normalizes rows to unit length by
            # default, which would make our own "tfidf_norm" magnitude feature
            # below a constant 1.0 for every non-empty document - i.e. no
            # signal at all. Leaving weights unnormalized lets document length
            # and vocabulary rarity actually show up in the magnitude.
            norm=None,
        )

    def fit_tfidf(self, texts: list[str]):
        """Fits/transforms with the configured vectorizer, falling back to
        min_df=1 if the corpus is too small/sparse for the configured min_df
        to leave any terms standing (common on small batches or test fixtures;
        the production 2000+ tweet corpus should rarely hit this path).
        """
        try:
            return self.vectorizer.fit_transform(texts)
        except ValueError:
            logger.warning(
                "TF-IDF pruning left no terms with min_df=%s on %d docs; retrying with min_df=1",
                self.vectorizer.min_df,
                len(texts),
            )
            self.vectorizer.set_params(min_df=1)
            return self.vectorizer.fit_transform(texts)

    def lexicon_score(self, text: str) -> float:
        """Bounded [-1, 1] score from bullish/bearish keyword hits, normalized
        by total hits so a longer tweet doesn't automatically score higher.
        """
        lowered = text.lower()
        bull_hits = sum(lowered.count(term) for term in self.bullish_terms)
        bear_hits = sum(lowered.count(term) for term in self.bearish_terms)
        total = bull_hits + bear_hits
        if total == 0:
            return 0.0
        return (bull_hits - bear_hits) / total

    def build_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Returns a copy of df with added tfidf_norm and lexicon_score columns."""
        if df.empty:
            out = df.copy()
            out["tfidf_norm"] = pd.Series(dtype=float)
            out["lexicon_score"] = pd.Series(dtype=float)
            return out

        tfidf_matrix = self.fit_tfidf(df["content"].tolist())
        tfidf_norm = np.asarray(tfidf_matrix.multiply(tfidf_matrix).sum(axis=1)).ravel() ** 0.5

        out = df.copy()
        out["tfidf_norm"] = tfidf_norm
        out["lexicon_score"] = out["content"].apply(self.lexicon_score)
        return out

    def top_terms(self, top_n: int = 20) -> list[str]:
        if not hasattr(self.vectorizer, "vocabulary_"):
            return []
        return sorted(self.vectorizer.vocabulary_, key=self.vectorizer.vocabulary_.get)[:top_n]
