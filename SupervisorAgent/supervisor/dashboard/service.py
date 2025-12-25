"""Dashboard aggregation service."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from supervisor.dashboard.models import DashboardEvent, HealthStatus, OverviewData
from supervisor.events import BaseEvent, EventType
from supervisor.heartbeat import HeartbeatServer
from supervisor.snapshot_models import SnapshotReport


class DashboardService:
    """Aggregates events/snapshots into simple dashboard-friendly structures."""

    def __init__(
        self,
        cfg: Dict[str, Any],
        events_dir: Path,
        heartbeat_server: HeartbeatServer,
        snapshot_provider,
        strategy_state_path: Optional[Path],
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.cfg = cfg or {}
        self.enabled = bool(self.cfg.get("enabled", True))
        self.events_dir = events_dir
        self.heartbeat_server = heartbeat_server
        self.snapshot_provider = snapshot_provider
        self.strategy_state_path = strategy_state_path
        self.logger = logger or logging.getLogger(__name__)

        overview_cfg = self.cfg.get("overview", {}) or {}
        self.pnl_window_minutes = int(overview_cfg.get("pnl_window_minutes", 60))

        health_cfg = self.cfg.get("health", {}) or {}
        self.snapshot_recent_minutes = int(health_cfg.get("require_snapshot_recent_minutes", 10))
        self.heartbeat_recent_seconds = int(health_cfg.get("require_heartbeat_recent_seconds", 60))

        self.max_events_default = int(self.cfg.get("max_events", 200))
        self.allowed_event_types = [str(t) for t in (self.cfg.get("events_types") or [])]

    # Public API
    def get_overview(self) -> OverviewData:
        now = datetime.now(timezone.utc)
        snapshot = self._latest_snapshot()
        heartbeat_state = self.heartbeat_server.get_state()

        events = self._load_recent_events(minutes=self.pnl_window_minutes)
        pnl_1h = self._calc_pnl(events)
        open_positions = 0
        if heartbeat_state.last_payload and "open_positions" in heartbeat_state.last_payload:
            try:
                open_positions = int(heartbeat_state.last_payload.get("open_positions", 0))
            except Exception:
                open_positions = 0

        overview = OverviewData(
            timestamp=now,
            total_pnl=0.0,  # Placeholder until full PnL tracking is integrated
            pnl_1h=pnl_1h,
            open_positions=open_positions,
            open_orders=0,
            strategy_mode=self._read_strategy_mode(),
            market_trend=snapshot.trend if snapshot else None,
            market_risk_level=snapshot.market_risk_level if snapshot else None,
        )
        return overview

    def get_health(self) -> HealthStatus:
        issues: List[str] = []
        now = datetime.now(timezone.utc)
        if not self.enabled:
            return HealthStatus(status="WARN", issues=["dashboard_disabled"])

        hb_state = self.heartbeat_server.get_state()
        last_hb = hb_state.last_heartbeat_time
        if last_hb:
            delta_hb = (now - last_hb).total_seconds()
            if delta_hb > self.heartbeat_recent_seconds:
                issues.append("heartbeat_stale")
        else:
            issues.append("heartbeat_missing")

        snapshot = self._latest_snapshot()
        last_snap = snapshot.timestamp if snapshot else None
        if last_snap:
            delta_snap = (now - last_snap).total_seconds() / 60
            if delta_snap > self.snapshot_recent_minutes:
                issues.append("snapshot_stale")
        else:
            issues.append("snapshot_missing")

        status = "OK"
        if any(i.endswith("missing") for i in issues):
            status = "WARN"
        if any("stale" in i for i in issues):
            status = "WARN"
        if len(issues) > 2:
            status = "FAIL"

        return HealthStatus(status=status, issues=issues, last_heartbeat_at=last_hb, last_snapshot_at=last_snap)

    def list_events(self, limit: Optional[int] = None, types: Optional[Sequence[str]] = None) -> List[DashboardEvent]:
        if not self.enabled:
            return []
        limit_val = limit or self.max_events_default
        allowed = [t.upper() for t in (types or self.allowed_event_types or [])]
        raw_events = self._load_recent_events(minutes=None, max_events=limit_val * 2)
        filtered: List[DashboardEvent] = []
        for ev in reversed(raw_events):  # newest first
            if allowed and ev.type.value not in allowed:
                continue
            filtered.append(
                DashboardEvent(
                    timestamp=ev.ts,
                    event_type=ev.type.value,
                    symbol=ev.data.get("symbol"),
                    details=ev.data,
                )
            )
            if len(filtered) >= limit_val:
                break
        return filtered

    # Internals
    def _latest_snapshot(self) -> Optional[SnapshotReport]:
        try:
            return self.snapshot_provider.latest_snapshot
        except Exception:
            return None

    def _calc_pnl(self, events: Iterable[BaseEvent]) -> float:
        pnl = 0.0
        for ev in events:
            if ev.type == EventType.ORDER_RESULT and isinstance(ev.data.get("pnl"), (int, float)):
                pnl += float(ev.data.get("pnl", 0.0))
        return pnl

    def _read_strategy_mode(self) -> Optional[str]:
        if not self.strategy_state_path or not self.strategy_state_path.exists():
            return None
        try:
            data = json.loads(self.strategy_state_path.read_text(encoding="utf-8"))
            return str(data.get("mode")) if data.get("mode") else None
        except Exception:
            return None

    def _load_recent_events(self, minutes: Optional[int] = None, max_events: Optional[int] = None) -> List[BaseEvent]:
        files = sorted(self.events_dir.glob("events_*.jsonl"), reverse=True)
        events: List[BaseEvent] = []
        cutoff: Optional[datetime] = None
        if minutes is not None:
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)

        for path in files:
            try:
                with path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        if not line.strip():
                            continue
                        raw = json.loads(line)
                        ts = datetime.fromisoformat(raw["ts"])
                        if cutoff and ts < cutoff:
                            continue
                        event = BaseEvent(
                            ts=ts,
                            type=EventType(raw["type"]),
                            source=raw.get("source", "unknown"),
                            data=raw.get("data", {}),
                        )
                        events.append(event)
            except Exception as exc:
                self.logger.warning("Failed to read events from %s: %s", path, exc)
            if max_events and len(events) >= max_events:
                break
        return events
