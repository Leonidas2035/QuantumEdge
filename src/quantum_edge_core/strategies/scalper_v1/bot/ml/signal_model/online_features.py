from __future__ import annotations

from typing import Dict, Optional

import numpy as np

from quantum_edge_core.strategies.scalper_v1.bot.ml.features.builder import FeatureBuilder


class OnlineFeatureBuilder:
    """
    Incrementally builds multi-timeframe features aligned with DatasetBuilder.
    """

    def __init__(self, warmup_seconds: int = 600, max_ticks: int = 1200):
        self._builder = FeatureBuilder(
            warmup_seconds=warmup_seconds, max_ticks=max_ticks
        )

    def update_microstructure(self, microstructure: Dict[str, float]) -> None:
        self._builder.update_microstructure(microstructure)

    def add_tick(
        self,
        timestamp: int,
        price: float,
        qty: float,
        side: str = "buy",
        microstructure: Optional[Dict[str, float]] = None,
    ) -> Optional[np.ndarray]:
        if microstructure:
            self._builder.update_microstructure(microstructure)
        return self._builder.add_tick(timestamp, price, qty, side=side)
