"""Utility rate limiter helpers."""

from __future__ import annotations

import threading
import time


class PerMinuteRateLimiter:
    """Simple per-minute rate limiter."""

    def __init__(self, max_calls_per_minute: int) -> None:
        self.max_calls = max(1, max_calls_per_minute)
        self._lock = threading.Lock()
        self._window_start = 0.0
        self._count = 0

    def allow(self) -> bool:
        now = time.time()
        with self._lock:
            elapsed = now - self._window_start
            if elapsed >= 60:
                self._window_start = now
                self._count = 0
            if self._count >= self.max_calls:
                return False
            self._count += 1
            return True
