"""Account/position snapshot state for quantum_edge_core.lock_bot."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class AccountState:
    long_qty: Optional[float] = None
    short_qty: Optional[float] = None
    long_avg_px: Optional[float] = None
    short_avg_px: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    initial_margin: Optional[float] = None
    maintenance_margin: Optional[float] = None
    equity: Optional[float] = None
    leverage: Optional[float] = None
    liq_price_long: Optional[float] = None
    liq_price_short: Optional[float] = None
    margin_usage: Optional[float] = None
    distance_to_liq_bps: Optional[float] = None
    last_account_ts: Optional[int] = None

    def update_timestamp(self, ts_ms: int) -> None:
        self.last_account_ts = ts_ms

    def net_delta_est(self) -> float:
        long_qty = self.long_qty or 0.0
        short_qty = self.short_qty or 0.0
        return long_qty - short_qty

    def compute_margin_usage(self) -> Optional[float]:
        if self.equity and (self.maintenance_margin or self.initial_margin):
            margin = (
                self.maintenance_margin
                if self.maintenance_margin is not None
                else self.initial_margin
            )
            if margin is None or self.equity <= 0:
                return None
            return float(margin) / float(self.equity)
        return None

    def compute_distance_to_liq_bps(
        self, mark_price: Optional[float]
    ) -> Optional[float]:
        if not mark_price or mark_price <= 0:
            return None
        distances = []
        if self.liq_price_long and self.liq_price_long > 0:
            distances.append(
                abs(mark_price - self.liq_price_long) / mark_price * 10000.0
            )
        if self.liq_price_short and self.liq_price_short > 0:
            distances.append(
                abs(mark_price - self.liq_price_short) / mark_price * 10000.0
            )
        if not distances:
            return None
        return min(distances)
