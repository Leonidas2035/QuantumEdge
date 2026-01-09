"""Account/position snapshot state for LockBotBTC."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class AccountState:
    long_qty: Optional[float] = None
    short_qty: Optional[float] = None
    long_avg_px: Optional[float] = None
    short_avg_px: Optional[float] = None
    margin_usage: Optional[float] = None
    distance_to_liq_bps: Optional[float] = None
    last_account_ts: Optional[int] = None

    def update_timestamp(self, ts_ms: int) -> None:
        self.last_account_ts = ts_ms

    def net_delta_est(self) -> float:
        long_qty = self.long_qty or 0.0
        short_qty = self.short_qty or 0.0
        return long_qty - short_qty

