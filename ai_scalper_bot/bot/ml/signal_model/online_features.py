from __future__ import annotations

from typing import Optional

from bot.ml.features.builder import FeatureBuilder


class OnlineFeatureBuilder:
    """
    Incrementally builds multi-timeframe features aligned with DatasetBuilder.
    """

    def __init__(self, warmup_seconds: int = 600, max_ticks: int = 1200):
        self._builder = FeatureBuilder(warmup_seconds=warmup_seconds, max_ticks=max_ticks)

    def add_tick(self, timestamp: int, price: float, qty: float, side: str = "buy") -> Optional[np.ndarray]:
        return self._builder.add_tick(timestamp, price, qty, side=side)
