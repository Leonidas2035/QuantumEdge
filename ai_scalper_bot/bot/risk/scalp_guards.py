"""Scalp-specific risk guardrails (counts, daily limits).

This module intentionally keeps the accounting lightweight. It tracks:
- open scalp positions count
- trades per day
- cumulative PnL percentage for the day (approximate placeholder)

The guards are designed to be risk-reducing only; they never increase exposure.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass


@dataclass
class ScalpGuardState:
    day: _dt.date
    open_positions: int = 0
    trades_today: int = 0
    loss_pct_today: float = 0.0


class ScalpGuard:
    """Minimal guardrail tracker for scalp entries."""

    def __init__(self, max_positions: int, max_trades: int, max_loss_pct: float) -> None:
        self.max_positions = max_positions
        self.max_trades = max_trades
        self.max_loss_pct = max_loss_pct
        self.state = ScalpGuardState(day=_dt.date.today())

    def _reset_if_new_day(self) -> None:
        today = _dt.date.today()
        if self.state.day != today:
            self.state = ScalpGuardState(day=today)

    def can_enter(self) -> tuple[bool, str]:
        self._reset_if_new_day()
        if self.state.open_positions >= self.max_positions:
            return False, "max_open_scalp_positions"
        if self.state.trades_today >= self.max_trades:
            return False, "max_daily_scalp_trades"
        if self.max_loss_pct > 0 and self.state.loss_pct_today <= -abs(self.max_loss_pct):
            return False, "max_daily_scalp_loss_pct"
        return True, "ok"

    def record_entry(self) -> None:
        self._reset_if_new_day()
        self.state.open_positions += 1
        self.state.trades_today += 1

    def record_exit(self) -> None:
        self._reset_if_new_day()
        if self.state.open_positions > 0:
            self.state.open_positions -= 1

    def record_pnl_pct(self, pnl_pct: float) -> None:
        """Approximate PnL tracking hook (optional)."""
        self._reset_if_new_day()
        self.state.loss_pct_today += pnl_pct
