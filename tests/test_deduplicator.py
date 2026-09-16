"""Tests for exact and near-duplicate detection (Bloom filter + banded
MinHash/LSH), including the specific tweet-length-edit case that caught the
original 4-gram/single-band configuration under-recalling during development.
"""
from src.processing.deduplicator import Deduplicator


def test_exact_duplicate_detected():
    dedup = Deduplicator(expected_items=1000)
    assert dedup.is_duplicate("trader_1", "Nifty breakout above 22000 #nifty50") is False
    assert dedup.is_duplicate("trader_1", "Nifty breakout above 22000 #nifty50") is True
    assert dedup.stats.exact_duplicates == 1


def test_near_duplicate_detected():
    # threshold 0.6: bigram Jaccard between these two (one word inserted) is ~0.625
    dedup = Deduplicator(expected_items=1000, near_dup_threshold=0.6)
    assert dedup.is_duplicate("trader_1", "Banknifty support held strong today at open") is False
    assert (
        dedup.is_duplicate("trader_2", "Banknifty support held strong today at the open") is True
    )
    assert dedup.stats.near_duplicates == 1


def test_distinct_content_not_flagged():
    dedup = Deduplicator(expected_items=1000)
    assert dedup.is_duplicate("trader_1", "Sensex crashes after weak GDP data") is False
    assert dedup.is_duplicate("trader_2", "Banknifty rallies on strong earnings") is False
    assert dedup.stats.unique == 2
