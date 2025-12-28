"""Run-level stats aggregation for SupervisorAgent."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class StatsAggregator:
    start_ts: float
    current_regime: str = "unknown"
    pnl_realized_total: Optional[float] = None
    pnl_unrealized_end: Optional[float] = None
    wins: int = 0
    losses: int = 0
    trades: int = 0
    blocked_actions_count: int = 0
    block_reasons: Dict[str, int] = field(default_factory=dict)
    regime_time_share: Dict[str, float] = field(default_factory=dict)
    errors_count: int = 0

    def __post_init__(self) -> None:
        self._lock = threading.Lock()
        self._regime_last_ts = self.start_ts

    def on_regime_change(self, new_regime: Optional[str], now_ts: Optional[float] = None) -> None:
        now = now_ts or time.time()
        regime = new_regime or "unknown"
        with self._lock:
            elapsed = max(0.0, now - self._regime_last_ts)
            self.regime_time_share[self.current_regime] = self.regime_time_share.get(self.current_regime, 0.0) + elapsed
            self.current_regime = regime
            self._regime_last_ts = now

    def on_trade_result(self, record: Dict[str, Any]) -> None:
        with self._lock:
            pnl = record.get("pnl_realized")
            if pnl is None:
                try:
                    qty = float(record.get("qty"))
                    entry = float(record.get("entry_price"))
                    exit_price = float(record.get("exit_price"))
                    pnl = (exit_price - entry) * qty
                except Exception:
                    pnl = None
            if pnl is not None:
                pnl = float(pnl)
                if self.pnl_realized_total is None:
                    self.pnl_realized_total = pnl
                else:
                    self.pnl_realized_total += pnl
                if pnl > 0:
                    self.wins += 1
                elif pnl < 0:
                    self.losses += 1
            self.trades += 1

    def on_block(self, reason_code: str, details: Optional[Dict[str, Any]] = None) -> None:
        _ = details
        with self._lock:
            self.blocked_actions_count += 1
            code = reason_code or "unknown"
            self.block_reasons[code] = self.block_reasons.get(code, 0) + 1

    def on_error(self) -> None:
        with self._lock:
            self.errors_count += 1

    def snapshot(self, now_ts: Optional[float] = None) -> Dict[str, Any]:
        now = now_ts or time.time()
        with self._lock:
            uptime = int(now - self.start_ts)
            top_reasons = sorted(self.block_reasons.items(), key=lambda item: item[1], reverse=True)[:5]
            pnl_total = self.pnl_realized_total
            return {
                "uptime_s": uptime,
                "pnl_total": pnl_total,
                "pnl_realized": self.pnl_realized_total,
                "pnl_realized_total": self.pnl_realized_total,
                "pnl_unrealized_end": self.pnl_unrealized_end,
                "wins": self.wins,
                "losses": self.losses,
                "trades": self.trades,
                "current_regime": self.current_regime,
                "blocked_actions_count": self.blocked_actions_count,
                "block_reasons_top": {k: v for k, v in top_reasons},
                "errors_count": self.errors_count,
            }

    def finalize(self, now_ts: Optional[float] = None) -> Dict[str, Any]:
        now = now_ts or time.time()
        self.on_regime_change(self.current_regime, now_ts=now)
        summary = self.snapshot(now_ts=now)
        summary["regime_time_share"] = dict(self.regime_time_share)
        summary["block_reasons"] = dict(self.block_reasons)
        return summary
