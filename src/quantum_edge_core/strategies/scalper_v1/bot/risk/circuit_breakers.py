"""Circuit breaker manager for runtime safety gates."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import time
from typing import Deque, Dict, Optional


@dataclass
class BreakerStatus:
    active: bool
    reason: Optional[str]
    active_until: float
    cooldown_remaining: float


class _WindowCounter:
    def __init__(self, limit: int, window_sec: float) -> None:
        self.limit = max(int(limit), 0)
        self.window_sec = max(float(window_sec), 0.0)
        self.events: Deque[float] = deque()

    def record(self, now: float) -> None:
        if self.limit <= 0 or self.window_sec <= 0:
            return
        self.events.append(now)
        self._prune(now)

    def _prune(self, now: float) -> None:
        if not self.events:
            return
        cutoff = now - self.window_sec
        while self.events and self.events[0] < cutoff:
            self.events.popleft()

    def triggered(self, now: float) -> bool:
        if self.limit <= 0 or self.window_sec <= 0:
            return False
        self._prune(now)
        return len(self.events) >= self.limit


class CircuitBreakerManager:
    """Tracks breaker conditions and returns active breaker state."""

    def __init__(self, cfg: Optional[Dict[str, object]] = None) -> None:
        cfg = cfg or {}
        self.cooldown_sec = float(cfg.get("cooldown_sec", 30.0) or 30.0)
        self.error_window = _WindowCounter(
            limit=int(cfg.get("exchange_errors_max", 5) or 0),
            window_sec=float(cfg.get("exchange_errors_window_sec", 60.0) or 60.0),
        )
        self.spread_window = _WindowCounter(
            limit=int(cfg.get("spread_wide_max", 3) or 0),
            window_sec=float(cfg.get("spread_wide_window_sec", 30.0) or 30.0),
        )
        self.latency_window = _WindowCounter(
            limit=int(cfg.get("latency_high_max", 5) or 0),
            window_sec=float(cfg.get("latency_high_window_sec", 60.0) or 60.0),
        )
        self.data_window = _WindowCounter(
            limit=int(cfg.get("data_stale_max", 3) or 0),
            window_sec=float(cfg.get("data_stale_window_sec", 60.0) or 60.0),
        )
        self.slippage_bps = float(cfg.get("slippage_bps", 0.0) or 0.0)
        self.spread_bps = float(cfg.get("spread_bps", 0.0) or 0.0)
        self.latency_ms = float(cfg.get("latency_ms", 0.0) or 0.0)
        self.max_drawdown_pct = float(cfg.get("max_drawdown_pct", 0.0) or 0.0)
        self.max_daily_loss_pct = float(cfg.get("max_daily_loss_pct", 0.0) or 0.0)
        self._active_until = 0.0
        self._active_reason: Optional[str] = None

    def _trip(self, reason: str, now: float) -> None:
        if self._active_until and now < self._active_until:
            return
        self._active_reason = reason
        self._active_until = now + self.cooldown_sec

    def record_exchange_error(self, now: Optional[float] = None) -> None:
        now = now or time.time()
        self.error_window.record(now)
        if self.error_window.triggered(now):
            self._trip("CB_EXCHANGE_ERRORS", now)

    def record_spread(self, spread_bps: Optional[float], now: Optional[float] = None) -> None:
        if spread_bps is None or self.spread_bps <= 0:
            return
        if spread_bps < self.spread_bps:
            return
        now = now or time.time()
        self.spread_window.record(now)
        if self.spread_window.triggered(now):
            self._trip("CB_SPREAD_WIDE_PERSISTENT", now)

    def record_latency(self, latency_ms: Optional[float], now: Optional[float] = None) -> None:
        if latency_ms is None or self.latency_ms <= 0:
            return
        if latency_ms < self.latency_ms:
            return
        now = now or time.time()
        self.latency_window.record(now)
        if self.latency_window.triggered(now):
            self._trip("CB_LATENCY_HIGH", now)

    def record_slippage(self, slippage_bps: Optional[float], now: Optional[float] = None) -> None:
        if slippage_bps is None or self.slippage_bps <= 0:
            return
        if slippage_bps < self.slippage_bps:
            return
        now = now or time.time()
        self._trip("CB_SLIPPAGE_SPIKE", now)

    def record_data_stale(self, stale: bool, now: Optional[float] = None) -> None:
        if not stale:
            return
        now = now or time.time()
        self.data_window.record(now)
        if self.data_window.triggered(now):
            self._trip("CB_DATA_STALE", now)

    def record_drawdown(self, drawdown_pct: Optional[float], now: Optional[float] = None) -> None:
        if drawdown_pct is None or self.max_drawdown_pct <= 0:
            return
        if drawdown_pct < self.max_drawdown_pct:
            return
        now = now or time.time()
        self._trip("CB_PNL_DRAWDOWN", now)

    def record_daily_loss(self, loss_pct: Optional[float], now: Optional[float] = None) -> None:
        if loss_pct is None or self.max_daily_loss_pct <= 0:
            return
        if loss_pct < self.max_daily_loss_pct:
            return
        now = now or time.time()
        self._trip("CB_DAILY_LOSS", now)

    def status(self, now: Optional[float] = None) -> BreakerStatus:
        now = now or time.time()
        if self._active_until and now >= self._active_until:
            self._active_until = 0.0
            self._active_reason = None
        active = bool(self._active_until and now < self._active_until)
        cooldown_remaining = max(self._active_until - now, 0.0) if active else 0.0
        return BreakerStatus(
            active=active,
            reason=self._active_reason,
            active_until=self._active_until,
            cooldown_remaining=cooldown_remaining,
        )

    def snapshot(self) -> Dict[str, object]:
        status = self.status()
        return {
            "active": status.active,
            "reason": status.reason,
            "cooldown_remaining_s": status.cooldown_remaining,
        }
