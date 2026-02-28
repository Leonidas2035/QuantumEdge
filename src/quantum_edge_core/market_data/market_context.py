"""MarketContext — Unified context for XGBoost (Hot Path) and LLM (Cold Path).

This dataclass captures a snapshot of multi-timeframe trend slopes
and whale wall features.  It is the single source of truth consumed
by both the low-latency XGBoost model (as a numerical dict) and the
Supervisor LLM (translated into a narrative string).
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Optional


@dataclass
class TimeframeSlope:
    """Linear regression slope for a single timeframe."""
    interval: str       # e.g. "5m", "15m", "1h", "4h"
    slope: float        # positive = bullish, negative = bearish
    r_squared: float    # goodness of fit
    candle_count: int   # datapoints used


@dataclass
class MarketContext:
    """Unified market context consumed by Hot Path (XGBoost) and Cold Path (LLM).

    Attributes
    ----------
    symbol : str
        Trading pair, e.g. "BTCUSDT".
    current_price : float
        Latest mid price.
    slopes : dict[str, TimeframeSlope]
        Multi-timeframe trend slopes keyed by interval.
    walls : dict[str, float]
        Whale wall features from OrderBookAggregator.
    timestamp : float
        Unix timestamp of context generation.
    """

    symbol: str = "BTCUSDT"
    current_price: float = 0.0
    slopes: Dict[str, TimeframeSlope] = field(default_factory=dict)
    walls: Dict[str, float] = field(default_factory=dict)
    timestamp: float = 0.0

    def to_feature_dict(self) -> Dict[str, Any]:
        """Flatten to numerical dict for XGBoost Hot Path.

        Returns
        -------
        dict
            e.g. {
                "price": 65500.0,
                "slope_5m": 0.003, "r2_5m": 0.85,
                "slope_15m": 0.007, "r2_15m": 0.91,
                "slope_1h": 0.012, "r2_1h": 0.88,
                "slope_4h": 0.015, "r2_4h": 0.92,
                "closest_bid_wall_btc": 25.4,
                "closest_bid_wall_dist_pct": -0.5,
                "closest_ask_wall_btc": 45.0,
                "closest_ask_wall_dist_pct": 0.2,
            }
        """
        features: Dict[str, Any] = {"price": self.current_price}

        for interval, s in self.slopes.items():
            features[f"slope_{interval}"] = round(s.slope, 6)
            features[f"r2_{interval}"] = round(s.r_squared, 4)

        features.update(self.walls)
        return features

    def to_dict(self) -> Dict[str, Any]:
        """Full serialization for debugging / persistence."""
        return asdict(self)
