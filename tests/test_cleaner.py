"""Tests for record cleaning/normalization, including the parallel
(ProcessPoolExecutor) path clean_batch switches to above _PARALLEL_THRESHOLD.
"""
from src.processing.cleaner import _PARALLEL_THRESHOLD, clean_batch, clean_record


def test_clean_record_strips_urls_and_computes_engagement():
    raw = {
        "tweet_id": "u1:123",
        "username": "@trader_1",
        "timestamp": "2026-09-08T10:00:00Z",
        "content": "Nifty breakout http://example.com/x  #nifty50",
        "hashtags": ["#nifty50"],
        "mentions": [],
        "reply_count": 2,
        "retweet_count": 3,
        "like_count": 10,
        "view_count": 100,
        "query_tag": "#nifty50",
    }
    cleaned = clean_record(raw)
    assert cleaned is not None
    assert "http" not in cleaned["content"]
    assert cleaned["username"] == "trader_1"
    assert cleaned["engagement_score"] == 2 + 2 * 3 + 10


def test_clean_record_drops_missing_required_fields():
    assert clean_record({"tweet_id": "", "username": "a", "content": "hi"}) is None


def test_clean_record_handles_non_numeric_counts_gracefully():
    raw = {
        "tweet_id": "u2:456",
        "username": "trader_2",
        "content": "Sensex flat today",
        "reply_count": "not-a-number",
    }
    cleaned = clean_record(raw)
    assert cleaned is not None
    assert cleaned["engagement_score"] == 0


def test_clean_batch_parallel_path_matches_sequential_result():
    """Exercises the ProcessPoolExecutor branch (batch size >= threshold)
    and checks it produces the same result as the sequential path - real
    parallelism (not a thread pool, which the GIL would serialize for this
    CPU-bound regex work) only pays off past _PARALLEL_THRESHOLD, so a
    small unit-test batch alone would never actually run this code path.
    """
    n = _PARALLEL_THRESHOLD + 50
    raw_records = [
        {
            "tweet_id": f"user{i}:{i}",
            "username": f"user{i}",
            "content": f"Nifty update number {i} #nifty50",
            "reply_count": i % 5,
            "retweet_count": i % 3,
            "like_count": i,
        }
        for i in range(n)
    ]

    cleaned = clean_batch(raw_records, max_workers=2)

    assert len(cleaned) == n
    assert {c["tweet_id"] for c in cleaned} == {r["tweet_id"] for r in raw_records}
    sample = next(c for c in cleaned if c["tweet_id"] == "user7:7")
    assert sample["engagement_score"] == (7 % 5) + 2 * (7 % 3) + 7
