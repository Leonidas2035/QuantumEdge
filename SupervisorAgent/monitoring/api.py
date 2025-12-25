"""Telemetry API helpers and manager."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, Optional

from .aggregator import TelemetryAggregator
from .alerts import AlertManager
from .event_store import TelemetryEventStore


EVENT_VERSION = "telemetry.v1"


@dataclass
class TelemetryConfig:
    max_event_size_kb: int = 32
    max_events_in_memory: int = 5000
    persist_path: Optional[str] = None
    alerts_thresholds: Optional[Dict[str, object]] = None
    alerts_cooldown_sec: int = 120


class TelemetryManager:
    def __init__(self, cfg: TelemetryConfig) -> None:
        persist_path = _safe_path(cfg.persist_path)
        self.store = TelemetryEventStore(max_events=cfg.max_events_in_memory, persist_path=persist_path)
        self.aggregator = TelemetryAggregator()
        self.alerts = AlertManager(cfg.alerts_thresholds or {}, cooldown_sec=cfg.alerts_cooldown_sec)

    def ingest(self, payload: Dict[str, object]) -> Dict[str, object]:
        event = normalize_event(payload)
        self.store.add(event)
        self.aggregator.process_event(event)
        self.alerts.evaluate(self.aggregator.summary().to_dict())
        return event

    def summary(self) -> Dict[str, object]:
        return self.aggregator.summary().to_dict()

    def events(self, limit: int = 200) -> list[Dict[str, object]]:
        return self.store.recent(limit=limit)

    def alerts_payload(self) -> Dict[str, object]:
        return {
            "active": self.alerts.active_alerts(),
            "recent": self.alerts.recent_alerts(),
        }

    def update_process_state(self, status_payload: Dict[str, object]) -> None:
        self.aggregator.update_process_state(status_payload)
        self.alerts.evaluate(self.aggregator.summary().to_dict())

    def record_policy(self, policy: Dict[str, object]) -> None:
        data = {
            "mode": policy.get("mode"),
            "allow_trading": policy.get("allow_trading"),
            "reason": policy.get("reason"),
        }
        event = {
            "event_version": EVENT_VERSION,
            "ts": int(time.time()),
            "source": "SupervisorAgent",
            "type": "policy",
            "symbol": None,
            "data": data,
        }
        self.store.add(event)
        self.aggregator.process_event(event)
        self.alerts.evaluate(self.aggregator.summary().to_dict())


def normalize_event(payload: Dict[str, object]) -> Dict[str, object]:
    now = int(time.time())
    event = {
        "event_version": str(payload.get("event_version") or EVENT_VERSION),
        "ts": _normalize_ts(payload.get("ts"), now),
        "source": str(payload.get("source") or "unknown"),
        "type": str(payload.get("type") or "unknown"),
        "symbol": payload.get("symbol"),
        "data": payload.get("data") if isinstance(payload.get("data"), dict) else {},
    }
    return event


def _normalize_ts(value: object, default: int) -> int:
    try:
        ts = float(value)
        if ts > 1e12:
            ts = ts / 1000.0
        return int(ts)
    except (TypeError, ValueError):
        return default


def _safe_path(value: Optional[str]):
    if not value:
        return None
    from pathlib import Path

    return Path(value)
