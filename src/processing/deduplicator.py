"""Deduplication: exact-match via bloom filter + hash set, near-duplicate via MinHash/LSH."""
from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from dataclasses import dataclass


class BloomFilter:
    """Space-efficient probabilistic set-membership test used as a fast
    pre-check before touching the (larger) exact hash set: O(k) per lookup
    with k = hash_count (~7-10) and only a few bits per item, versus storing
    and re-hashing full tweet text on every check. No false negatives, so a
    real duplicate is never missed - a new item only rarely pays for an
    extra (cheap) exact-set lookup it didn't need.
    """

    def __init__(self, capacity: int, error_rate: float = 0.001):
        self.size = self._optimal_size(capacity, error_rate)
        self.hash_count = self._optimal_hash_count(self.size, capacity)
        self.bit_array = bytearray(math.ceil(self.size / 8))

    @staticmethod
    def _optimal_size(n: int, p: float) -> int:
        return max(8, int(-(n * math.log(p)) / (math.log(2) ** 2)))

    @staticmethod
    def _optimal_hash_count(m: int, n: int) -> int:
        return max(1, round((m / max(n, 1)) * math.log(2)))

    def _hashes(self, item: str):
        h1 = int(hashlib.md5(item.encode("utf-8")).hexdigest(), 16)
        h2 = int(hashlib.sha1(item.encode("utf-8")).hexdigest(), 16)
        for i in range(self.hash_count):
            yield (h1 + i * h2) % self.size

    def add(self, item: str) -> None:
        for bit_index in self._hashes(item):
            self.bit_array[bit_index // 8] |= 1 << (bit_index % 8)

    def __contains__(self, item: str) -> bool:
        return all(
            self.bit_array[bit_index // 8] & (1 << (bit_index % 8))
            for bit_index in self._hashes(item)
        )


@dataclass
class DedupStats:
    total_seen: int = 0
    exact_duplicates: int = 0
    near_duplicates: int = 0
    unique: int = 0


class Deduplicator:
    """Two-stage dedup pipeline.

    Stage 1 (exact): bloom filter pre-check -> hash set confirm. O(1)
    amortized per item, and the bloom filter keeps memory sub-linear in the
    common "definitely new" case.

    Stage 2 (near-duplicate, e.g. the same tip retweeted-with-comment or
    lightly reworded): MinHash signature buckets (a lightweight LSH).
    Instead of comparing a new tweet's shingles against every previously
    seen tweet (O(n) per item, O(n^2) overall), we only compare against
    tweets that landed in the same signature bucket - candidates that are
    already likely similar. This keeps near-dup checks close to O(1)
    amortized even as the corpus grows, which matters for the "10x more
    data" scalability requirement.
    """

    def __init__(
        self,
        expected_items: int = 200_000,
        error_rate: float = 0.001,
        near_dup_threshold: float = 0.6,
        num_bands: int = 12,
        rows_per_band: int = 1,
    ):
        self._bloom = BloomFilter(expected_items, error_rate)
        self._exact_hashes: set[str] = set()
        self._shingles_by_key: dict[str, set[str]] = {}
        self._bands: list[dict[tuple, list[str]]] = [defaultdict(list) for _ in range(num_bands)]
        self.near_dup_threshold = near_dup_threshold
        self.num_bands = num_bands
        self.rows_per_band = rows_per_band
        self.num_minhashes = num_bands * rows_per_band
        self.stats = DedupStats()

    @staticmethod
    def _content_key(username: str, content: str) -> str:
        normalized = content.strip().lower()
        return hashlib.sha256(f"{username}:{normalized}".encode("utf-8")).hexdigest()

    @staticmethod
    def _shingles(text: str, k: int = 2) -> set[str]:
        """Word k-shingles. k=2 (bigrams) rather than the 4-5 grams typical
        for long-document near-dup detection: a tweet is only ~7-15 words,
        so a single inserted/removed word (a common retweet-with-comment
        edit) shifts most 4-gram windows and can more than halve Jaccard
        similarity even though the tweets clearly say the same thing.
        Bigrams keep some phrase-level signal (unlike pure bag-of-words)
        while staying far more tolerant of small edits at this text length.
        """
        tokens = text.lower().split()
        if len(tokens) < k:
            return {" ".join(tokens)} if tokens else set()
        return {" ".join(tokens[i : i + k]) for i in range(len(tokens) - k + 1)}

    @staticmethod
    def _stable_hash(shingle: str, seed: int) -> int:
        """Deterministic hash (unlike Python's built-in `hash()`, which is
        randomized per-process via PYTHONHASHSEED for security). Using
        `hash()` here would make bucket assignment - and therefore which
        near-duplicates get caught - non-reproducible across runs/processes,
        which matters for testability and for comparing pipeline runs.
        """
        digest = hashlib.md5(f"{seed}:{shingle}".encode("utf-8")).digest()
        return int.from_bytes(digest[:8], "big")

    def _minhash_signature(self, shingles: set[str]) -> tuple:
        if not shingles:
            return tuple([0] * self.num_minhashes)
        return tuple(
            min(self._stable_hash(s, seed) for s in shingles) for seed in range(self.num_minhashes)
        )

    def _band_keys(self, signature: tuple) -> list[tuple]:
        return [
            signature[i : i + self.rows_per_band]
            for i in range(0, self.num_minhashes, self.rows_per_band)
        ]

    def _find_near_duplicate(self, shingles: set[str]) -> str | None:
        """Lookup only - does not register `key` anywhere. Registration is a
        separate step (`_register`) so a tweet that turns out to be a
        duplicate never ends up referenced from a bucket without a
        corresponding `_shingles_by_key` entry (that mismatch previously
        caused a KeyError on a later lookup against the same bucket).
        """
        signature = self._minhash_signature(shingles)
        band_keys = self._band_keys(signature)

        candidates: set[str] = set()
        for band, band_key in zip(self._bands, band_keys):
            candidates.update(band[band_key])

        for candidate_key in candidates:
            existing = self._shingles_by_key[candidate_key]
            union = shingles | existing
            if union and len(shingles & existing) / len(union) >= self.near_dup_threshold:
                return candidate_key
        return None

    def _register(self, key: str, shingles: set[str]) -> None:
        signature = self._minhash_signature(shingles)
        for band, band_key in zip(self._bands, self._band_keys(signature)):
            band[band_key].append(key)
        self._shingles_by_key[key] = shingles

    def is_duplicate(self, username: str, content: str, near_dup_check: bool = True) -> bool:
        self.stats.total_seen += 1
        key = self._content_key(username, content)

        if key in self._bloom and key in self._exact_hashes:
            self.stats.exact_duplicates += 1
            return True

        shingles = self._shingles(content)
        if near_dup_check and self._find_near_duplicate(shingles) is not None:
            self.stats.near_duplicates += 1
            return True

        self._bloom.add(key)
        self._exact_hashes.add(key)
        self._register(key, shingles)
        self.stats.unique += 1
        return False
