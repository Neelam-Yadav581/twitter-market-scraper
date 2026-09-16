# Qode Market Intel

A data collection and analysis system that turns Indian stock-market discussion on
X (Twitter) into quantitative trading signals: scrapes hashtag search results,
cleans and deduplicates the tweets, stores them in Parquet, and produces a composite
sentiment signal per hashtag with confidence intervals, plus charts.

## Project structure

```
config/config.yaml       Central configuration - hashtags, scrape target, login profile, thresholds
src/scraper/              Selenium scraper, rate limiter, DOM selectors
src/processing/           Cleaning, deduplication, Parquet storage
src/analysis/             TF-IDF + lexicon signal extraction, aggregation, visualization
scripts/run_scraper.py    Runs the live scrape
scripts/run_pipeline.py   Runs clean -> dedup -> store -> analyze -> visualize
tests/                     pytest unit tests
docs/TECHNICAL_APPROACH.md   Design rationale and complexity notes
data/raw/                 Raw scraped batch JSON (gitignored)
data/processed/           Cleaned/deduplicated Parquet output (gitignored)
reports/                  Signal report (CSV) and charts (PNG) - the analysis deliverable
```

## 1. Set up the environment

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1        # PowerShell. Use .venv\Scripts\activate.bat for cmd.exe
python -m pip install -r requirements.txt
```

You'll also need Google Chrome installed - the scraper downloads a matching
ChromeDriver automatically via `webdriver-manager`.

## 2. Log into X in a dedicated Chrome profile

The scraper reuses an already-logged-in Chrome profile rather than logging in itself.

1. Make sure Chrome is fully closed (check with
   `tasklist /FI "IMAGENAME eq chrome.exe"` - it should report nothing running).
2. Launch Chrome pointed at a new, dedicated profile folder:
   ```bash
   & "C:\Program Files\Google\Chrome\Application\chrome.exe" --user-data-dir="D:\SeleniumProfile"
   ```
   (any empty folder path works - this matches the default in `config.yaml`)
3. Log into X in that window normally, and wait for the home feed to load.
4. Close that Chrome window.

## 3. Configure `config/config.yaml`

Fields you're likely to change:

| Field | What it controls |
|---|---|
| `scraper.user_data_dir` | Path to the Chrome profile from step 2 |
| `scraper.hashtags` | Which hashtags to search (defaults: `#nifty50`, `#sensex`, `#intraday`, `#banknifty`) |
| `scraper.target_tweets` | Total tweets to collect across all hashtags in a single "one go" run |
| `scraper.headless` | Set `true` to run Chrome without a visible window |
| `processing.near_duplicate_threshold` | How similar two tweets must be to count as duplicates (0-1) |
| `analysis.bullish_lexicon` / `bearish_lexicon` | Keywords used to score tweet sentiment |

Everything else has a sensible default and rarely needs changing.

## 4. Scrape data

**Option A - one go:** a single run covering every configured hashtag up to
`target_tweets`:
```bash
python scripts\run_scraper.py --out data\raw\raw_scrape.json
```

**Option B - batches:** shorter, separate runs (one hashtag and a smaller limit each),
useful for spreading collection out over time:
```bash
python scripts\run_scraper.py --tags "#nifty50"   --limit 500 --out data\raw\batch1.json
python scripts\run_scraper.py --tags "#sensex"    --limit 500 --out data\raw\batch2.json
python scripts\run_scraper.py --tags "#intraday"  --limit 500 --out data\raw\batch3.json
python scripts\run_scraper.py --tags "#banknifty" --limit 500 --out data\raw\batch4.json
```

`--tags` and `--limit` also work together for a quick test, e.g.
`--tags "#nifty50" --limit 2`.

## 5. Generate the report

Point `run_pipeline.py` at whatever you scraped - a single file, or every batch file
at once (it merges and deduplicates across all of them automatically):

```bash
# one go
python scripts\run_pipeline.py --input data\raw\raw_scrape.json

# batches - glob pattern or comma-separated list both work
python scripts\run_pipeline.py --input "data\raw\batch*.json"
```

## 6. Check the results

- `reports/composite_signals.csv` - composite sentiment signal per hashtag, with tweet
  volume, engagement totals, and a 95% confidence interval
- `reports/signal_by_hashtag.png` - bar chart of the above
- `reports/volume_timeseries.png` - tweet volume over time
- `reports/engagement_distribution.png` - engagement histogram
- `data/processed/*.parquet` - the cleaned, deduplicated dataset itself

The pipeline also prints the signal table to the console when it finishes.

## Run tests

```bash
pytest -q
```
