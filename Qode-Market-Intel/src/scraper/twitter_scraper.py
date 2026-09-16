"""Selenium-based X (Twitter) scraper for public hashtag search results.

IMPORTANT: Scraping X/Twitter without its official (paid) API sits in a legal
and Terms-of-Service gray area. This module is provided for educational /
personal research use only. It requires the operator's own logged-in
session (X requires authentication to view live search results) and a
human-paced browsing pattern. Run it locally with your own account; do not
use it for bulk commercial data resale or high-frequency automated polling.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional
from urllib.parse import quote

from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

from src.scraper import selectors
from src.scraper.rate_limiter import ExponentialBackoff, TokenBucketRateLimiter
from src.utils.logging_config import setup_logger
from src.utils.text_utils import (
    clean_tweet_text,
    extract_hashtags,
    extract_mentions,
    parse_engagement_count,
)

logger = setup_logger(__name__)


@dataclass
class TweetRecord:
    tweet_id: str
    username: str
    timestamp: str
    content: str
    hashtags: list[str] = field(default_factory=list)
    mentions: list[str] = field(default_factory=list)
    reply_count: int = 0
    retweet_count: int = 0
    like_count: int = 0
    view_count: int = 0
    query_tag: str = ""

    def engagement_score(self) -> int:
        return self.reply_count + 2 * self.retweet_count + self.like_count


class TwitterScraper:
    """Drives a real (visible, human-paced) browser session against X search.

    Requires `config.scraper.user_data_dir` to point at a Chrome profile
    already logged into X manually beforehand - see README.md "Login setup".
    This scraper never submits credentials itself: an earlier version did
    (reading X_USERNAME/X_PASSWORD and automating the login form), but that
    was the single riskiest action for tripping X's anti-automation
    detection, and it did exactly that during development (see
    docs/TECHNICAL_APPROACH.md). Reusing an already-authenticated profile
    removes that step entirely.

    Design notes:
    - Rate limiting + randomized delays reduce (never eliminate) the chance
      of tripping anti-bot detection; there is no reliable way to guarantee
      evasion, and this project does not attempt to defeat CAPTCHAs.
    - Selectors are centralized in `selectors.py` since X's DOM changes often.
    """

    def __init__(self, config: dict):
        self.config = config["scraper"]
        self.rate_limiter = TokenBucketRateLimiter(
            max_actions=self.config["rate_limit"]["max_actions_per_window"],
            window_seconds=self.config["rate_limit"]["window_seconds"],
        )
        self.backoff = ExponentialBackoff(
            base_seconds=self.config["rate_limit"]["backoff_base_seconds"],
            max_seconds=self.config["rate_limit"]["backoff_max_seconds"],
        )
        self.driver: Optional[webdriver.Chrome] = None
        self._seen_ids: set[str] = set()  # O(1) in-session dedup by tweet id

    @staticmethod
    def _find_processes_using_profile(user_data_dir: str) -> list[int]:
        """Best-effort (Windows-only) check for a chrome.exe already running
        against this profile directory. A stale process from a prior crashed
        or interrupted run holding the profile lock is exactly what produced
        a generic 'Chrome instance exited' crash in testing - this turns that
        into an actionable error naming the PID to kill, checked before the
        expensive driver-launch attempt rather than after it fails.
        """
        try:
            result = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
                    f"Where-Object {{ $_.CommandLine -like '*{user_data_dir}*' }} | "
                    "Select-Object -ExpandProperty ProcessId",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return [int(pid) for pid in result.stdout.split() if pid.strip().isdigit()]
        except (subprocess.SubprocessError, OSError, ValueError):
            return []  # non-Windows or PowerShell unavailable: skip the check, don't fail the run

    def start(self) -> None:
        options = Options()
        if self.config.get("headless"):
            options.add_argument("--headless=new")
        width, height = self.config.get("window_size", [1366, 900])
        options.add_argument(f"--window-size={width},{height}")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)

        user_data_dir = self.config.get("user_data_dir")
        if not user_data_dir:
            raise RuntimeError(
                "scraper.user_data_dir is not set in config.yaml. This scraper requires "
                "a Chrome profile already logged into X manually - see README.md "
                "'Login setup' for the one-time setup steps."
            )
        existing_pids = self._find_processes_using_profile(user_data_dir)
        if existing_pids:
            raise RuntimeError(
                f"Chrome is already running against user_data_dir={user_data_dir} "
                f"(PID(s): {existing_pids}) - a second instance can't share the same "
                f"profile and will crash with an opaque 'Chrome instance exited' error. "
                f"Close that window fully, or run: taskkill /F /PID {existing_pids[0]} /T"
            )
        options.add_argument(f"--user-data-dir={user_data_dir}")
        profile_directory = self.config.get("profile_directory")
        if profile_directory:
            options.add_argument(f"--profile-directory={profile_directory}")

        try:
            log_path = Path("logs/chromedriver.log")
            log_path.parent.mkdir(parents=True, exist_ok=True)
            # Truncated per-run rather than appended: ChromeDriver's verbose
            # log is only useful for diagnosing *this* run's start() failure,
            # and left to accumulate across many runs it grows unbounded (it
            # reached 16MB+ after a day of testing). Best-effort: a previous
            # run's process still holding the file open (Windows file lock)
            # shouldn't block this one from starting.
            try:
                log_path.write_text("", encoding="utf-8")
            except OSError:
                pass
            service = Service(ChromeDriverManager().install(), log_output=str(log_path))
            self.driver = webdriver.Chrome(service=service, options=options)
        except WebDriverException:
            logger.exception(
                "Failed to start Chrome WebDriver; see logs/chromedriver.log for the "
                "underlying Chrome-side error (Selenium's own message is often generic)"
            )
            raise

        self.driver.set_page_load_timeout(self.config.get("page_load_timeout", 30))
        logger.info("Chrome driver started")

    def stop(self) -> None:
        if not self.driver:
            return
        self.driver.quit()
        logger.info("Chrome driver stopped")

    def is_logged_in(self) -> bool:
        """Checks whether the current session is already authenticated via the
        persistent profile logged in manually beforehand.

        Checks for a logged-in-only UI marker (account switcher / home nav)
        rather than a tweet article: a near-empty or slow-to-render feed
        would otherwise look indistinguishable from "not logged in".
        """
        self.driver.get("https://x.com/home")
        marker_selector = ", ".join(selectors.LOGGED_IN_MARKER_CANDIDATES)
        try:
            WebDriverWait(self.driver, 25).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, marker_selector))
            )
            return True
        except TimeoutException:
            self._debug_dump("not_logged_in")
            return False

    def _debug_dump(self, label: str) -> None:
        """Captures a screenshot + current URL/title when something doesn't
        match expectations, so a DOM/selector mismatch can be diagnosed from
        the logs instead of having to reproduce it live.
        """
        debug_dir = Path("logs")
        debug_dir.mkdir(parents=True, exist_ok=True)
        shot_path = debug_dir / f"{label}.png"
        try:
            self.driver.save_screenshot(str(shot_path))
            logger.error(
                "Debug dump [%s]: url=%s title=%r screenshot=%s",
                label,
                self.driver.current_url,
                self.driver.title,
                shot_path,
            )
        except WebDriverException:
            logger.exception("Failed to capture debug screenshot for %s", label)

    def _is_rate_limited(self) -> bool:
        try:
            page_text = self.driver.page_source
        except WebDriverException:
            return False
        return any(marker.lower() in page_text.lower() for marker in selectors.RATE_LIMIT_MARKERS)

    def search_hashtag(self, hashtag: str, target_count: int) -> Iterator[TweetRecord]:
        query = hashtag if hashtag.startswith("#") else f"#{hashtag}"
        # quote(): an un-encoded '#' starts a URL *fragment*, not a query
        # value - "search?q=#nifty50" sends X an EMPTY q param (everything
        # from '#' on is dropped from the request), which is why an earlier
        # run returned generic/trending tweets instead of anything on-topic.
        url = selectors.SEARCH_URL_TEMPLATE.format(query=quote(query))
        self.rate_limiter.acquire()
        self.driver.get(url)
        logger.info("Opened search for %s", query)

        collected = 0
        stale_scroll_rounds = 0
        max_rounds = self.config.get("max_scroll_attempts", 500)
        # page_source serializes X's entire (heavy) DOM to a string - doing
        # that every loop was the single biggest per-iteration cost. A real
        # rate-limit/anti-bot page persists across many iterations once it
        # appears, so checking every few loops instead of every loop costs
        # negligible detection latency for a large cut in wall-clock time.
        rate_limit_check_interval = self.config.get("rate_limit_check_interval", 4)

        for iteration in range(max_rounds):
            if collected >= target_count:
                break

            if iteration % rate_limit_check_interval == 0 and self._is_rate_limited():
                delay = self.backoff.wait()
                logger.warning("Rate-limit/anti-bot page detected; backed off %.1fs", delay)
                self.driver.get(url)
                continue
            self.backoff.reset()

            try:
                articles = self.driver.find_elements(By.CSS_SELECTOR, selectors.TWEET_ARTICLE)
            except (NoSuchElementException, WebDriverException):
                articles = []

            new_this_round = 0
            for article in articles:
                try:
                    record = self._parse_article(article, query_tag=query)
                except StaleElementReferenceException:
                    continue
                if record is None or record.tweet_id in self._seen_ids:
                    continue
                self._seen_ids.add(record.tweet_id)
                new_this_round += 1
                collected += 1
                yield record
                if collected >= target_count:
                    break

            stale_scroll_rounds = 0 if new_this_round else stale_scroll_rounds + 1
            if stale_scroll_rounds >= 8:
                logger.info("No new tweets after several scrolls for %s; stopping early", query)
                break

            low, high = self.config.get("scroll_pause_range", [1.5, 3.5])
            self.driver.execute_script("window.scrollBy(0, window.innerHeight * 3);")
            TokenBucketRateLimiter.human_delay(low, high)
            self.rate_limiter.acquire()

        logger.info("Collected %d tweets for %s", collected, query)

    def _parse_article(self, article, query_tag: str) -> Optional[TweetRecord]:
        try:
            content_el = article.find_element(By.CSS_SELECTOR, selectors.TWEET_TEXT)
            raw_content = content_el.text
        except NoSuchElementException:
            raw_content = ""

        try:
            time_el = article.find_element(By.CSS_SELECTOR, selectors.TWEET_TIME)
            timestamp = time_el.get_attribute("datetime")
        except NoSuchElementException:
            timestamp = ""

        try:
            user_block = article.find_element(By.CSS_SELECTOR, selectors.TWEET_USER_NAME)
            username = user_block.text.split("\n")[0]
        except NoSuchElementException:
            username = "unknown"

        if not raw_content and not timestamp:
            return None  # promoted/ad cards or malformed nodes

        content = clean_tweet_text(raw_content)
        tweet_id = f"{username}:{timestamp}:{hash(content) & 0xFFFFFFFF}"

        def _count(selector: str) -> int:
            try:
                el = article.find_element(By.CSS_SELECTOR, selector)
                return parse_engagement_count(el.get_attribute("aria-label") or el.text)
            except NoSuchElementException:
                return 0

        return TweetRecord(
            tweet_id=tweet_id,
            username=username,
            timestamp=timestamp,
            content=content,
            hashtags=extract_hashtags(raw_content),
            mentions=extract_mentions(raw_content),
            reply_count=_count(selectors.TWEET_REPLY_COUNT),
            retweet_count=_count(selectors.TWEET_RETWEET_COUNT),
            like_count=_count(selectors.TWEET_LIKE_COUNT),
            view_count=_count(selectors.TWEET_VIEW_COUNT),
            query_tag=query_tag,
        )
