# Technical Approach

## 1. Data collection under the "no paid APIs" constraint

X's official API tiers that support search are paid; the assignment explicitly rules
out paid APIs and the Twitter API. That leaves browser automation as the only option,
X requires an authenticated session to view live search results, so the scraper needs
*some* logged-in session before it can search at all. Rather than automating the
login form itself, the operator logs in once, manually, in a dedicated Chrome profile
(see README.md "Log into X in a dedicated Chrome profile"); the scraper reuses that
session (`scraper.user_data_dir` in `config.yaml`) and never reads or submits
credentials. An earlier version did support automating the login form directly (via
environment-variable credentials), but a real account was flagged by X's automation
detection ("We've temporarily limited your login") on essentially the first attempt
during development, despite the rate-limiting/fingerprint-reduction measures below -
that mode was removed in favor of the persistent-profile approach, which moves the
actual authentication event to a normal, manual, human login instead.

Once authenticated, the scraper paginates a hashtag search page by scrolling and
parsing the rendered tweet cards.

**Anti-bot / rate-limit handling** (deliberately conservative, not adversarial):
- A token-bucket rate limiter (`TokenBucketRateLimiter`, deque-based) caps actions per
  rolling time window.
- Randomized delays between scroll actions avoid a fixed, easily fingerprinted cadence.
- `--disable-blink-features=AutomationControlled` and disabling Chrome's own
  automation-extension/infobar remove the most obvious automation fingerprints.
- If the page shows a rate-limit/anti-bot marker (`selectors.RATE_LIMIT_MARKERS`) *and*
  no tweets are currently rendered (a genuine block replaces the feed entirely, unlike
  an incidental phrase match inside ordinary tweet text), the scraper backs off
  exponentially with jitter rather than retrying immediately.

This reduces detection risk; it does not defeat CAPTCHAs or eliminate the risk of an
account being challenged - not just a theoretical caveat, given the account-lock
above. Anyone extending this scraper should budget for that happening again rather
than treat it as unlikely.

## 2. Data structures and their complexity

| Structure | Used for | Why | Complexity |
|---|---|---|---|
| `deque` of timestamps | Rate limiting | O(1) amortized push/evict from either end for a sliding time window, vs. re-scanning/sorting a list | O(1) amortized per `acquire()` |
| Bloom filter (`bytearray` bit array) | Exact-dedup pre-check | Sub-linear memory (a few bits/item) vs. storing full tweet text; no false negatives | O(k) per check, k = hash count (~7-10) |
| `set` of SHA-256 hashes | Exact-dedup confirmation | O(1) average lookup/insert; only consulted after a bloom-filter hit, so it's rarely the bottleneck | O(1) average |
| MinHash signature buckets, banded (`list[dict[tuple, list]]`) | Near-duplicate detection | Naive near-dup detection compares every new tweet's shingle set against every previous one (O(n²) over a run). Banded MinHash buckets limit comparisons to items already likely similar, without requiring every hash function to agree at once | O(1) amortized per check, assuming reasonable bucket sizes |
| pandas `sort_values` (see `aggregator.py`'s final ordering step) | Ranking hashtags by composite signal volume | A full sort, not a top-k selection - deliberately simple, since there are only ever as many rows as configured hashtags (a handful), where a `heapq`/partial-selection approach would add complexity for no measurable benefit | O(n log n), n = number of hashtags (irrelevant in practice at this n; would be worth revisiting only if ranking across hundreds of groups) |
| Parquet (columnar, partitioned by date) | Storage | Columnar compression suits repeated-value columns (hashtags, query_tag); date partitioning lets future large-scale reads skip irrelevant files entirely | I/O roughly proportional to partitions touched, not total rows |

## 3. Deduplication design in more detail

Two independent problems, two techniques:

1. **Exact duplicates** (the same tweet scraped twice across overlapping scroll
   positions, or two hashtag searches surfacing the same tweet): a SHA-256 hash of
   `(username, normalized_content)`, pre-filtered through a Bloom filter sized for the
   configured expected item count and false-positive rate (`config.yaml ->
   processing.bloom_filter`).

2. **Near-duplicates** (a tip copy-pasted by multiple accounts, or reworded slightly):
   word-bigram shingles + Jaccard similarity above a configurable threshold
   (`processing.near_duplicate_threshold`, default 0.6). Bigrams, not the 4-5 grams
   typical for long-document near-dup detection, because a tweet is only ~7-15 words -
   a single inserted/removed word (a common retweet-with-comment edit) shifts most
   longer n-gram windows and can more than halve Jaccard similarity even though the
   tweets clearly say the same thing; an earlier 4-gram/0.85 configuration was caught
   failing exactly this case in `tests/test_deduplicator.py` during development. Lower
   thresholds trade some precision (topically similar-but-distinct tweets sharing
   common finance vocabulary could collide) for recall on genuine near-duplicates;
   the threshold is deliberately a config value so it can be tuned against real data.
   Comparing a new tweet against *every* previously seen tweet is O(n) per item and
   O(n²) over a full run - fine at 2,000 tweets, painful at 20,000+. MinHash signatures,
   split into multiple bands that are OR'd together (standard LSH banding - a single
   band requiring all hashes to match gives poor recall), bucket similar items
   together so only same-bucket candidates are compared, keeping the check close to
   O(1) amortized as the corpus grows.

## 4. Text-to-signal conversion

Two complementary features per tweet:
- **TF-IDF vector magnitude** (`SignalExtractor.build_features`): a proxy for how
  distinctive/information-dense a tweet's language is relative to the corpus.
- **Custom finance lexicon score**: counts bullish vs. bearish keyword hits (tuned for
  Indian-market phrasing - "upper circuit", "lower circuit", "breakout", etc. - which
  general-purpose sentiment lexicons don't cover), normalized to `[-1, 1]`.

These combine into a **composite signal per hashtag**: an engagement-weighted mean of
the lexicon score (tweets with more replies/retweets/likes count for more), alongside
tweet volume and average TF-IDF magnitude as supporting context.

**Confidence intervals** use bootstrap resampling rather than a normal approximation,
because the lexicon score is bounded and its distribution is often skewed (many
tweets score exactly 0 - no lexicon hits at all).

## 5. Memory-efficient visualization

- Aggregate-first plots (signal-by-hashtag, volume-over-time) never touch per-tweet
  data directly - they plot pre-aggregated series, so their memory footprint is
  independent of the raw tweet count.
- Any plot that does need per-tweet data (e.g. an engagement-score histogram) uses
  **reservoir sampling** to cap the number of points drawn at a fixed size regardless
  of how many rows exist upstream, reading from `ParquetStore.iter_batches()` so the
  full dataset is never materialized in memory at once.

## 6. Scaling to 10x the data

- **Storage**: Parquet partitioning by `collected_date` already means a 10x larger
  dataset mostly adds more, still-small files rather than one huge file; downstream
  reads can filter by date range instead of scanning everything.
- **Collection itself**: `run_scraper.py` supports running in many short, separated
  batches (`--tags`/`--limit` per invocation) rather than one long session, with
  `run_pipeline.py --input "data/raw/batch*.json"` merging and deduplicating across
  all of them at analysis time. This is the practical path to 10x the data without
  10x the continuous automated session length in one sitting - both a scalability
  lever and (per section 1's empirical finding) a detection-risk mitigation.
- **Dedup**: the Bloom filter's `capacity` config parameter should scale with expected
  item count to keep the false-positive rate low; MinHash bucketing complexity is
  roughly independent of total corpus size as long as bucket occupancy stays bounded.
- **Signal extraction**: `TfidfVectorizer(max_features=...)` bounds vocabulary size
  regardless of corpus size; for a truly large corpus, `run_pipeline.py` would need to
  switch from loading the whole run into one DataFrame to a batch-wise fit/transform
  over `ParquetStore.iter_batches()` (e.g. `HashingVectorizer`, which needs no
  vocabulary-fitting pass at all). Note that `iter_batches()` (and `read_all()`) can
  read either one specific parquet file or every file in `processed_dir` - the latter
  is for deliberate cumulative analysis across many past runs, not what a single run's
  own report should do (mixing in unrelated historical files there was a real bug
  during development, since fixed - `run_pipeline.py` now reads back only its own
  run's file).
- **Concurrency**: the four hashtag searches in `run_scraper.py` are deliberately
  sequential - concurrent browser sessions would multiply the anti-bot detection
  surface, and section 1's empirical account-lock finding is exactly why that
  trade-off isn't worth it here. The *processing* side is different: `clean_batch()`
  (`src/processing/cleaner.py`) runs each record through `clean_record()` - a pure,
  per-record function with no shared state - via `concurrent.futures.ProcessPoolExecutor`
  once a batch is large enough (`_PARALLEL_THRESHOLD`, default 2000 records) for real
  multi-core parallelism to outweigh process spawn/pickling overhead; a plain
  sequential pass handles smaller batches, since that overhead would otherwise
  dominate the trivial per-record work. A process pool rather than a thread pool
  because this work is CPU-bound (regex substitutions) - a thread pool's threads
  would just serialize on the GIL instead of actually running in parallel. Signal
  extraction could extend the same pattern per-partition once corpus size makes it
  worthwhile; at the current scale, TF-IDF fitting itself is the cheaper step.

## 7. Ethical / ToS note

Automated scraping of X without its paid API is against X's Terms of Service. This
project is built for educational/personal-research purposes as specified by the
assignment ("no paid APIs allowed... consider using Selenium"). Anyone running the
live scraper should use their own account, keep the built-in rate limiting intact,
and avoid redistributing or commercializing collected data.
