"""Market snapshot state for LockBotBTC."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class MarketState:
    mark_price: Optional[float] = None
    vwap_d: Optional[float] = None
    band_1u: Optional[float] = None
    band_1l: Optional[float] = None
    band_2u: Optional[float] = None
    band_2l: Optional[float] = None
    avwap: Optional[dict] = None
    liq_heatmap: Optional[dict] = None
    funding_rate: Optional[float] = None
    last_market_ts: Optional[int] = None

    def update_timestamp(self, ts_ms: int) -> None:
        self.last_market_ts = ts_ms

