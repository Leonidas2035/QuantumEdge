"""Minimal heartbeat data structures and in-memory server."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Mapping, Optional


@dataclass
class HeartbeatPayload:
    """Payload sent by the trading engine."""

    uptime_s: Optional[float] = None
    pnl: Optional[float] = None
    active_positions: Optional[int] = None
    last_tick_ts: Optional[datetime] = None
    mode: Optional[str] = None
    details: Optional[Mapping[str, Any]] = None
    equity: Optional[float] = None
    realized_pnl_today: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    open_positions_notional: Optional[float] = None
    base_currency: Optional[str] = None
    trading_day: Optional[date] = None


@dataclass
class HeartbeatState:
    """Represents the latest heartbeat status."""

    last_heartbeat_time: Optional[datetime]
    last_payload: Optional[HeartbeatPayload]
    heartbeat_timeout_s: float

    @property
    def status(self) -> str:
        """Return human-readable status."""

        if self.last_heartbeat_time is None:
            return "NO_DATA"

        delta = datetime.now(timezone.utc) - self.last_heartbeat_time
        if delta.total_seconds() <= self.heartbeat_timeout_s:
            return "HEALTHY"
        return "STALE"


class HeartbeatServer:
    """In-memory heartbeat aggregator."""

    def __init__(self, heartbeat_timeout_s: float) -> None:
        self._heartbeat_timeout_s = heartbeat_timeout_s
        self._state = HeartbeatState(
            last_heartbeat_time=None,
            last_payload=None,
            heartbeat_timeout_s=heartbeat_timeout_s,
        )

    @staticmethod
    def _parse_timestamp(value: Any) -> Optional[datetime]:
        if value is None:
            return None
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed
            except ValueError:
                return None
        return None

    @staticmethod
    def _parse_date(value: Any) -> Optional[date]:
        if value is None:
            return None
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value).date()
            except ValueError:
                return None
        return None

    def update_heartbeat(self, data: Mapping[str, Any]) -> None:
        """Update the heartbeat state from a payload mapping."""

        payload = HeartbeatPayload(
            uptime_s=data.get("uptime_s"),
            pnl=data.get("pnl"),
            active_positions=data.get("active_positions"),
            last_tick_ts=self._parse_timestamp(data.get("last_tick_ts")),
            mode=data.get("mode"),
            details=data.get("details"),
            equity=data.get("equity"),
            realized_pnl_today=data.get("realized_pnl_today"),
            unrealized_pnl=data.get("unrealized_pnl"),
            open_positions_notional=data.get("open_positions_notional"),
            base_currency=data.get("base_currency"),
            trading_day=self._parse_date(data.get("trading_day")),
        )
        self._state = HeartbeatState(
            last_heartbeat_time=datetime.now(timezone.utc),
            last_payload=payload,
            heartbeat_timeout_s=self._heartbeat_timeout_s,
        )

    def get_state(self) -> HeartbeatState:
        """Return the latest heartbeat state."""

        return self._state


def heartbeat_to_risk_summary(state: HeartbeatState) -> Optional[Mapping[str, Any]]:
    """Convert heartbeat state into a compact risk summary."""

    if state.last_payload is None:
        return None
    hb = state.last_payload
    trading_day = hb.trading_day
    if trading_day is None and hb.last_tick_ts:
        trading_day = hb.last_tick_ts.date()
    return {
        "equity": hb.equity,
        "realized_pnl_today": hb.realized_pnl_today,
        "unrealized_pnl": hb.unrealized_pnl,
        "open_positions_notional": hb.open_positions_notional,
        "base_currency": hb.base_currency,
        "trading_day": trading_day,
    }
