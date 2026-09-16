"""Token-bucket rate limiter with jitter and exponential backoff for scraping actions."""
from __future__ import annotations

import random
import time
from collections import deque


class TokenBucketRateLimiter:
    """Bounds actions to `max_actions` per rolling `window_seconds` using a deque of timestamps.

    A deque gives O(1) amortized push/pop from both ends, which is exactly what a
    sliding time-window needs: evict expired entries off the front one at a time,
    append the new one at the back. No need to rescan or sort the whole history.
    """

    def __init__(self, max_actions: int, window_seconds: float):
        self.max_actions = max_actions
        self.window_seconds = window_seconds
        self._timestamps: deque[float] = deque()

    def acquire(self) -> None:
        now = time.monotonic()
        while self._timestamps and now - self._timestamps[0] > self.window_seconds:
            self._timestamps.popleft()

        if len(self._timestamps) >= self.max_actions:
            sleep_for = self.window_seconds - (now - self._timestamps[0])
            if sleep_for > 0:
                time.sleep(sleep_for)
            self._timestamps.popleft()

        self._timestamps.append(time.monotonic())

    @staticmethod
    def human_delay(low: float = 1.5, high: float = 3.5) -> None:
        """Randomized pause so requests don't land at a robotic, fixed interval."""
        time.sleep(random.uniform(low, high))


class ExponentialBackoff:
    """Backoff used when a rate-limit wall / CAPTCHA / anti-bot page is detected."""

    def __init__(self, base_seconds: float = 5, max_seconds: float = 300):
        self.base_seconds = base_seconds
        self.max_seconds = max_seconds
        self._attempt = 0

    def wait(self) -> float:
        delay = min(self.max_seconds, self.base_seconds * (2**self._attempt))
        delay *= random.uniform(0.8, 1.2)  # jitter avoids synchronized retry storms
        self._attempt += 1
        time.sleep(delay)
        return delay

    def reset(self) -> None:
        self._attempt = 0
