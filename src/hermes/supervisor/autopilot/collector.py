"""Metrics collector for QuantumEdge runtime."""

from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class HealthStatus:
    status: str
    evidence: List[str] = field(default_factory=list)


@dataclass
class MetricsSnapshot:
    ts: float
    mode: str
    counters: Dict[str, Any]
    breaker_trips: Dict[str, Any]
    breaker_active: bool
    breaker_reason: Optional[str]
    tick_age_ms: Optional[int]
    book_age_ms: Optional[int]
    last_error: Optional[str]
    last_error_ts: Optional[float]
    policy_mode: Optional[str]
    policy_allow_trading: Optional[bool]
    raw: Dict[str, Any]
    health: HealthStatus


class MetricsCollector:
    def __init__(self, metrics_url: str, metrics_path: Path) -> None:
        self.metrics_url = metrics_url
        self.metrics_path = metrics_path

    def collect(self) -> MetricsSnapshot:
        raw = self._fetch_metrics()
        now = time.time()
        counters = (
            raw.get("counters", {}) if isinstance(raw.get("counters"), dict) else {}
        )
        breaker_trips = (
            raw.get("breaker_trips", {})
            if isinstance(raw.get("breaker_trips"), dict)
            else {}
        )
        breaker = raw.get("breaker", {}) if isinstance(raw.get("breaker"), dict) else {}
        health = self._assess_health(raw)
        return MetricsSnapshot(
            ts=float(raw.get("ts", now) or now),
            mode=str(raw.get("mode") or raw.get("app_mode") or "unknown"),
            counters=counters,
            breaker_trips=breaker_trips,
            breaker_active=bool(breaker.get("active", False)),
            breaker_reason=breaker.get("reason"),
            tick_age_ms=_safe_int(raw.get("tick_age_ms")),
            book_age_ms=_safe_int(raw.get("book_age_ms")),
            last_error=raw.get("last_error"),
            last_error_ts=raw.get("last_error_ts"),
            policy_mode=raw.get("policy_mode"),
            policy_allow_trading=raw.get("policy_allow_trading"),
            raw=raw,
            health=health,
        )

    def _fetch_metrics(self) -> Dict[str, Any]:
        if self.metrics_url:
            payload = self._fetch_http(self.metrics_url)
            if payload is not None:
                return payload
        if self.metrics_path.exists():
            try:
                return json.loads(self.metrics_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return {}
        return {}

    def _fetch_http(self, url: str) -> Optional[Dict[str, Any]]:
        try:
            with urllib.request.urlopen(url, timeout=1.5) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception:
            return None

    def _assess_health(self, raw: Dict[str, Any]) -> HealthStatus:
        evidence: List[str] = []
        if not raw:
            return HealthStatus(status="UNKNOWN", evidence=["metrics_missing"])
        breaker = raw.get("breaker", {})
        if isinstance(breaker, dict) and breaker.get("active"):
            evidence.append("breaker_active")
        state = raw.get("state")
        if state == "stopped":
            evidence.append("state_stopped")
        tick_age = _safe_int(raw.get("tick_age_ms"))
        book_age = _safe_int(raw.get("book_age_ms"))
        if tick_age is None:
            evidence.append("tick_age_missing")
        if book_age is None:
            evidence.append("book_age_missing")
        last_error = raw.get("last_error")
        if last_error:
            evidence.append(f"last_error:{last_error}")
        status = "OK"
        if any(item.startswith("breaker_active") for item in evidence):
            status = "WARN"
        if "state_stopped" in evidence:
            status = "FAIL"
        last_error_ts = raw.get("last_error_ts")
        if last_error and last_error_ts is not None:
            try:
                age = time.time() - float(last_error_ts)
                if age < 300:
                    status = "FAIL"
            except (TypeError, ValueError):
                pass
        if "metrics_missing" in evidence:
            status = "UNKNOWN"
        return HealthStatus(status=status, evidence=evidence)


def _safe_int(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None
