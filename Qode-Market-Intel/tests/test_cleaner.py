from src.processing.cleaner import clean_record


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
