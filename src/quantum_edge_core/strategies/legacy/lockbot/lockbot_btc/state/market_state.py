"""Market snapshot state for quantum_edge_core.strategies.legacy.lockbot."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Optional


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
    volatility_bps: Optional[float] = None
    volatility_window: int = 60
    _marks: Deque[float] = field(init=False)

    def __post_init__(self) -> None:
        self._marks = deque(maxlen=max(int(self.volatility_window), 2))

    def update_timestamp(self, ts_ms: int) -> None:
        self.last_market_ts = ts_ms

    def update_mark_price(self, price: float) -> None:
        self.mark_price = price
        self._marks.append(price)
        if len(self._marks) < 2:
            return
        returns = []
        for i in range(1, len(self._marks)):
            prev = self._marks[i - 1]
            cur = self._marks[i]
            if prev <= 0:
                continue
            returns.append((cur - prev) / prev)
        if not returns:
            return
        mean = sum(returns) / len(returns)
        var = sum((r - mean) ** 2 for r in returns) / len(returns)
        self.volatility_bps = (var**0.5) * 10000.0
