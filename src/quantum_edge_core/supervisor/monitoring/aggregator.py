"""Telemetry aggregation for monitoring and policy signals."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, Optional


@dataclass
class TelemetrySummary:
    last_seen_ts: Optional[int]
    trades_5m: int
    trades_1h: int
    error_rate_1m: int
    latency_ms_avg: Optional[float]
    latency_ms_p95: Optional[float]
    pnl_day: Optional[float]
    drawdown_day: Optional[float]
    equity: Optional[float]
    policy_mode: Optional[str]
    policy_allow_trading: Optional[bool]
    policy_reason: Optional[str]
    status_state: Optional[str]
    restarts: Optional[int]
    restart_rate_per_hour: Optional[float]

    def to_dict(self) -> Dict[str, object]:
        return {
            "last_seen_ts": self.last_seen_ts,
            "trades_5m": self.trades_5m,
            "trades_1h": self.trades_1h,
            "error_rate_1m": self.error_rate_1m,
            "latency_ms_avg": self.latency_ms_avg,
            "latency_ms_p95": self.latency_ms_p95,
            "pnl_day": self.pnl_day,
            "drawdown_day": self.drawdown_day,
            "equity": self.equity,
            "policy_mode": self.policy_mode,
            "policy_allow_trading": self.policy_allow_trading,
            "policy_reason": self.policy_reason,
            "status_state": self.status_state,
            "restarts": self.restarts,
            "restart_rate_per_hour": self.restart_rate_per_hour,
        }


class TelemetryAggregator:
    def __init__(
        self,
        error_window_sec: int = 60,
        trade_window_sec: int = 300,
        trade_window_hour_sec: int = 3600,
        latency_window_sec: int = 60,
    ) -> None:
        self.error_window_sec = error_window_sec
        self.trade_window_sec = trade_window_sec
        self.trade_window_hour_sec = trade_window_hour_sec
        self.latency_window_sec = latency_window_sec
        self._error_ts: Deque[int] = deque()
        self._trade_ts: Deque[int] = deque()
        self._trade_ts_hour: Deque[int] = deque()
        self._latency: Deque[tuple[int, float]] = deque()
        self._last_seen_ts: Optional[int] = None
        self._pnl_day: Optional[float] = None
        self._drawdown_day: Optional[float] = None
        self._equity: Optional[float] = None
        self._policy_mode: Optional[str] = None
        self._policy_allow_trading: Optional[bool] = None
        self._policy_reason: Optional[str] = None
        self._status_state: Optional[str] = None
        self._restarts: Optional[int] = None
        self._restart_rate_per_hour: Optional[float] = None

    def process_event(self, event: Dict[str, object]) -> None:
        ts = int(event.get("ts") or time.time())
        self._last_seen_ts = ts
        event_type = str(event.get("type") or "unknown")
        data = event.get("data") or {}
        if event_type in {"order", "fill"}:
            self._trade_ts.append(ts)
            self._trade_ts_hour.append(ts)
        elif event_type == "error":
            self._error_ts.append(ts)
        elif event_type == "latency":
            loop_ms = data.get("loop_ms") if isinstance(data, dict) else None
            try:
                loop_val = float(loop_ms)
            except (TypeError, ValueError):
                loop_val = None
            if loop_val is not None:
                self._latency.append((ts, loop_val))
        elif event_type == "pnl":
            if isinstance(data, dict):
                self._equity = _safe_float(data.get("equity"), self._equity)
                self._pnl_day = _safe_float(data.get("pnl_day"), self._pnl_day)
                self._drawdown_day = _safe_float(data.get("drawdown_day"), self._drawdown_day)
        elif event_type == "policy":
            if isinstance(data, dict):
                self._policy_mode = str(data.get("mode") or self._policy_mode)
                allow = data.get("allow_trading")
                if allow is not None:
                    self._policy_allow_trading = bool(allow)
                reason = data.get("reason")
                if reason:
                    self._policy_reason = str(reason)
        elif event_type == "status":
            if isinstance(data, dict):
                state = data.get("state")
                if state:
                    self._status_state = str(state)

    def update_process_state(self, status_payload: Dict[str, object]) -> None:
        if not isinstance(status_payload, dict):
            return
        restarts = status_payload.get("restarts")
        if restarts is not None:
            try:
                self._restarts = int(restarts)
            except (TypeError, ValueError):
                self._restarts = None
        last_exit_raw = status_payload.get("last_exit_time")
        restart_rate = None
        if self._restarts and last_exit_raw:
            try:
                last_exit_ts = _parse_iso(last_exit_raw)
                hours = max((time.time() - last_exit_ts) / 3600.0, 0.01)
                restart_rate = self._restarts / hours
            except Exception:
                restart_rate = None
        self._restart_rate_per_hour = restart_rate

    def summary(self) -> TelemetrySummary:
        now = int(time.time())
        self._prune(now)
        latency_vals = [val for _, val in self._latency]
        latency_avg = sum(latency_vals) / len(latency_vals) if latency_vals else None
        latency_p95 = _percentile(latency_vals, 0.95) if latency_vals else None
        return TelemetrySummary(
            last_seen_ts=self._last_seen_ts,
            trades_5m=len(self._trade_ts),
            trades_1h=len(self._trade_ts_hour),
            error_rate_1m=len(self._error_ts),
            latency_ms_avg=latency_avg,
            latency_ms_p95=latency_p95,
            pnl_day=self._pnl_day,
            drawdown_day=self._drawdown_day,
            equity=self._equity,
            policy_mode=self._policy_mode,
            policy_allow_trading=self._policy_allow_trading,
            policy_reason=self._policy_reason,
            status_state=self._status_state,
            restarts=self._restarts,
            restart_rate_per_hour=self._restart_rate_per_hour,
        )

    def _prune(self, now: int) -> None:
        _prune_deque(self._error_ts, now - self.error_window_sec)
        _prune_deque(self._trade_ts, now - self.trade_window_sec)
        _prune_deque(self._trade_ts_hour, now - self.trade_window_hour_sec)
        while self._latency and self._latency[0][0] < now - self.latency_window_sec:
            self._latency.popleft()


def _prune_deque(queue: Deque[int], cutoff: int) -> None:
    while queue and queue[0] < cutoff:
        queue.popleft()


def _percentile(values: list[float], pct: float) -> Optional[float]:
    if not values:
        return None
    if pct <= 0:
        return min(values)
    if pct >= 1:
        return max(values)
    values_sorted = sorted(values)
    idx = int(round((len(values_sorted) - 1) * pct))
    return values_sorted[idx]


def _safe_float(value: object, default: Optional[float]) -> Optional[float]:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_iso(value: object) -> float:
    if not value:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value)
    if text.endswith("Z"):
        text = text.replace("Z", "+00:00")
    from datetime import datetime

    return datetime.fromisoformat(text).timestamp()
