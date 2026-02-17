"""Quality monitoring for autopilot decisions."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, List, Tuple

from supervisor.autopilot.collector import MetricsSnapshot


@dataclass
class QualityIssue:
    code: str
    severity: str
    details: Dict[str, Any]


class QualityMonitor:
    def __init__(
        self,
        breaker_storm_threshold: int,
        breaker_storm_window_sec: int,
        coverage_min: float,
        coverage_window_sec: int,
        latency_p95_ms: int,
        data_stale_ms: int,
        data_stale_window_sec: int,
        policy_mismatch_reject_ratio: float,
    ) -> None:
        self.breaker_storm_threshold = breaker_storm_threshold
        self.breaker_storm_window_sec = breaker_storm_window_sec
        self.coverage_min = coverage_min
        self.coverage_window_sec = coverage_window_sec
        self.latency_p95_ms = latency_p95_ms
        self.data_stale_ms = data_stale_ms
        self.data_stale_window_sec = data_stale_window_sec
        self.policy_mismatch_reject_ratio = policy_mismatch_reject_ratio
        self.history: Deque[MetricsSnapshot] = deque(maxlen=500)

    def update(self, snapshot: MetricsSnapshot) -> List[QualityIssue]:
        self.history.append(snapshot)
        now = snapshot.ts
        issues: List[QualityIssue] = []

        breaker_count = self._delta_counter(
            "breaker_trips", now, self.breaker_storm_window_sec
        )
        if breaker_count >= self.breaker_storm_threshold > 0:
            issues.append(
                QualityIssue("AP_BREAKER_STORM", "FAIL", {"count": breaker_count})
            )

        coverage = self._coverage_ratio(now, self.coverage_window_sec)
        if coverage is not None and coverage < self.coverage_min:
            issues.append(
                QualityIssue("AP_METRICS_DEGRADED", "WARN", {"coverage": coverage})
            )

        latency = snapshot.raw.get("latency_p95_ms")
        if latency is not None and self.latency_p95_ms > 0:
            try:
                latency_val = float(latency)
                if latency_val >= self.latency_p95_ms:
                    issues.append(
                        QualityIssue(
                            "AP_METRICS_DEGRADED",
                            "WARN",
                            {"latency_p95_ms": latency_val},
                        )
                    )
            except (TypeError, ValueError):
                pass

        stale_window = self._stale_ratio(now, self.data_stale_window_sec)
        if stale_window and stale_window.get("stale"):
            issues.append(QualityIssue("AP_DATA_STALE", "FAIL", stale_window))

        policy_mismatch_ratio = self._policy_mismatch_ratio(
            now, self.coverage_window_sec
        )
        if (
            policy_mismatch_ratio is not None
            and policy_mismatch_ratio >= self.policy_mismatch_reject_ratio
        ):
            issues.append(
                QualityIssue(
                    "AP_POLICY_MISMATCH", "FAIL", {"ratio": policy_mismatch_ratio}
                )
            )

        return issues

    def acceptance_metrics(self, now: float, window_sec: int) -> Dict[str, Any]:
        window = max(int(window_sec), 1)
        breaker_count = self._delta_counter("breaker_trips", now, window)
        coverage = self._coverage_ratio(now, window)
        cutoff = now - window
        recent = [s for s in self.history if s.ts >= cutoff]
        tick_age_ms = [s.tick_age_ms for s in recent if s.tick_age_ms is not None]
        book_age_ms = [s.book_age_ms for s in recent if s.book_age_ms is not None]
        max_tick_age_ms = max(tick_age_ms) if tick_age_ms else None
        max_book_age_ms = max(book_age_ms) if book_age_ms else None
        return {
            "breaker_count": breaker_count,
            "coverage": coverage,
            "max_tick_age_ms": max_tick_age_ms,
            "max_book_age_ms": max_book_age_ms,
        }

    def _delta_counter(self, key: str, now: float, window: int) -> int:
        if not self.history:
            return 0
        cutoff = now - window
        recent = [s for s in self.history if s.ts >= cutoff]
        if len(recent) < 2:
            return 0
        start = (
            recent[0].breaker_trips if key == "breaker_trips" else recent[0].counters
        )
        end = (
            recent[-1].breaker_trips if key == "breaker_trips" else recent[-1].counters
        )
        return _sum_dict(end) - _sum_dict(start)

    def _coverage_ratio(self, now: float, window: int) -> float | None:
        cutoff = now - window
        recent = [s for s in self.history if s.ts >= cutoff]
        if len(recent) < 2:
            return None
        orders = _delta_counter(recent, "orders")
        rejects = _delta_rejects(recent)
        total = orders + rejects
        if total <= 0:
            return None
        return orders / total

    def _stale_ratio(self, now: float, window: int) -> Dict[str, Any]:
        cutoff = now - window
        recent = [s for s in self.history if s.ts >= cutoff]
        if not recent:
            return {}
        stale = False
        tick_age = recent[-1].tick_age_ms
        book_age = recent[-1].book_age_ms
        if self.data_stale_ms > 0:
            if tick_age is not None and tick_age >= self.data_stale_ms:
                stale = True
            if book_age is not None and book_age >= self.data_stale_ms:
                stale = True
        return {
            "stale": stale,
            "tick_age_ms": tick_age,
            "book_age_ms": book_age,
        }

    def _policy_mismatch_ratio(self, now: float, window: int) -> float | None:
        cutoff = now - window
        recent = [s for s in self.history if s.ts >= cutoff]
        if len(recent) < 2:
            return None
        rejects = _delta_rejects(recent)
        if rejects <= 0:
            return None
        mismatch = _delta_rejects(
            recent,
            keys=(
                "SCHEMA_HASH_MISMATCH",
                "MODEL_MISSING",
                "MODEL_MISSING_H1",
                "MODEL_MISSING_H5",
                "MODEL_MISSING_H30",
            ),
        )
        return mismatch / rejects if rejects else None


def _sum_dict(payload: Dict[str, Any]) -> int:
    total = 0
    for value in payload.values():
        try:
            total += int(value)
        except (TypeError, ValueError):
            continue
    return total


def _delta_counter(recent: List[MetricsSnapshot], key: str) -> int:
    start = recent[0].counters.get(key, 0)
    end = recent[-1].counters.get(key, 0)
    try:
        return int(end) - int(start)
    except (TypeError, ValueError):
        return 0


def _delta_rejects(
    recent: List[MetricsSnapshot], keys: Tuple[str, ...] | None = None
) -> int:
    def _count(snapshot: MetricsSnapshot) -> int:
        total = 0
        for k, v in snapshot.counters.items():
            if not str(k).startswith("reject:"):
                continue
            reason = str(k).replace("reject:", "")
            if keys and reason not in keys:
                continue
            try:
                total += int(v)
            except (TypeError, ValueError):
                continue
        return total

    return _count(recent[-1]) - _count(recent[0])
