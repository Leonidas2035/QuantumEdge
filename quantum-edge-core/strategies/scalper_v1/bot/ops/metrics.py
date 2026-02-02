"""Runtime metrics tracking and snapshot helper."""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class MetricsTracker:
    counters: Counter = field(default_factory=Counter)
    last_error: Optional[str] = None
    last_error_ts: Optional[float] = None
    breaker_trips: Counter = field(default_factory=Counter)
    last_updated: float = field(default_factory=time.time)

    def incr(self, key: str, amount: int = 1) -> None:
        self.counters[key] += amount

    def record_error(self, reason: str) -> None:
        self.last_error = reason
        self.last_error_ts = time.time()
        self.incr("errors")

    def record_reject(self, reason: str) -> None:
        self.incr(f"reject:{reason}")

    def record_breaker(self, reason: str) -> None:
        self.breaker_trips[reason] += 1

    def snapshot(self, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = {
            "ts": time.time(),
            "counters": dict(self.counters),
            "breaker_trips": dict(self.breaker_trips),
            "last_error": self.last_error,
            "last_error_ts": self.last_error_ts,
        }
        if extra:
            payload.update(extra)
        return payload
