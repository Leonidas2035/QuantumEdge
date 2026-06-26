"""Simple circuit breaker for LLM moderation."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List


@dataclass
class CircuitBreaker:
    failure_threshold: int
    window_sec: int
    open_sec: int
    failures: List[float] = field(default_factory=list)
    open_until: float = 0.0

    def allow(self) -> bool:
        now = time.time()
        if now < self.open_until:
            return False
        self._prune(now)
        return True

    def record_success(self) -> None:
        self.failures.clear()
        self.open_until = 0.0

    def record_failure(self) -> None:
        now = time.time()
        self._prune(now)
        self.failures.append(now)
        if len(self.failures) >= self.failure_threshold:
            self.open_until = now + self.open_sec

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_sec
        self.failures = [ts for ts in self.failures if ts >= cutoff]

    def state(self) -> dict:
        return {
            "open": time.time() < self.open_until,
            "open_until": self.open_until,
            "failures": len(self.failures),
            "failure_threshold": self.failure_threshold,
            "window_sec": self.window_sec,
            "open_sec": self.open_sec,
        }
