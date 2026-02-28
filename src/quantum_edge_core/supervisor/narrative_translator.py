"""NarrativeTranslator — Converts numerical MarketContext to LLM-ready text.

This module bridges the Hot Path (numerical) and Cold Path (LLM) by
translating the unified ``MarketContext`` into a concise, human-readable
narrative that the Supervisor's Gemini LLM can reason over.
"""

from __future__ import annotations

import logging
from typing import List

from quantum_edge_core.market_data.market_context import MarketContext

logger = logging.getLogger("NarrativeTranslator")


class NarrativeTranslator:
    """Translates a ``MarketContext`` into a text narrative for the LLM Supervisor.

    Design: The narrative is deterministic and compact, optimised for
    structured LLM consumption.  It avoids ambiguity and uses consistent
    terminology (bullish/bearish/neutral, strong/weak fit).
    """

    # Slope thresholds for trend classification
    BULLISH_THRESHOLD = 0.001
    BEARISH_THRESHOLD = -0.001

    # R² thresholds for confidence
    STRONG_FIT = 0.7
    WEAK_FIT = 0.3

    @classmethod
    def translate(cls, context: MarketContext) -> str:
        """Convert MarketContext to a structured narrative string.

        Parameters
        ----------
        context : MarketContext
            Unified context with MTF slopes and whale wall features.

        Returns
        -------
        str
            Example: "Macro 4h is bullish (slope=0.012, R²=0.91).
            Ask wall detected at +0.2% (45.0 BTC). Bid wall is
            far at -0.5% (25.4 BTC)."
        """
        parts: List[str] = []

        # 1. Price header
        parts.append(f"Price: {context.current_price:.2f} {context.symbol}.")

        # 2. MTF Trend Analysis (ordered: 4h → 1h → 15m → 5m)
        ordered_tfs = ["4h", "1h", "15m", "5m"]
        tf_labels = {"4h": "Macro 4h", "1h": "Mid 1h", "15m": "Short 15m", "5m": "Micro 5m"}

        for tf in ordered_tfs:
            if tf in context.slopes:
                s = context.slopes[tf]
                trend = cls._classify_trend(s.slope)
                confidence = cls._classify_confidence(s.r_squared)
                parts.append(
                    f"{tf_labels[tf]} is {trend} (slope={s.slope:.4f}, "
                    f"R²={s.r_squared:.2f}, {confidence} fit, {s.candle_count} candles)."
                )

        # 3. Whale Wall Analysis
        walls = context.walls
        if walls:
            ask_btc = walls.get("closest_ask_wall_btc", 0.0)
            ask_dist = walls.get("closest_ask_wall_dist_pct", 0.0)
            bid_btc = walls.get("closest_bid_wall_btc", 0.0)
            bid_dist = walls.get("closest_bid_wall_dist_pct", 0.0)

            if ask_btc > 0:
                parts.append(
                    f"Ask wall detected at {ask_dist:+.2f}% ({ask_btc:.1f} BTC)."
                )
            else:
                parts.append("No significant ask wall within 2% depth.")

            if bid_btc > 0:
                parts.append(
                    f"Bid wall detected at {bid_dist:+.2f}% ({bid_btc:.1f} BTC)."
                )
            else:
                parts.append("No significant bid wall within 2% depth.")

        # 4. Overall bias
        bias = cls._compute_bias(context)
        parts.append(f"Overall bias: {bias}.")

        return " ".join(parts)

    @classmethod
    def _classify_trend(cls, slope: float) -> str:
        if slope > cls.BULLISH_THRESHOLD:
            return "bullish"
        elif slope < cls.BEARISH_THRESHOLD:
            return "bearish"
        return "neutral"

    @classmethod
    def _classify_confidence(cls, r_squared: float) -> str:
        if r_squared >= cls.STRONG_FIT:
            return "strong"
        elif r_squared >= cls.WEAK_FIT:
            return "moderate"
        return "weak"

    @classmethod
    def _compute_bias(cls, context: MarketContext) -> str:
        """Derive overall bias from weighted MTF slopes.

        Higher timeframes carry more weight:
            4h=4, 1h=3, 15m=2, 5m=1
        """
        weights = {"4h": 4.0, "1h": 3.0, "15m": 2.0, "5m": 1.0}
        total_weight = 0.0
        weighted_slope = 0.0

        for tf, s in context.slopes.items():
            w = weights.get(tf, 1.0)
            weighted_slope += s.slope * w
            total_weight += w

        if total_weight == 0:
            return "INSUFFICIENT_DATA"

        avg = weighted_slope / total_weight
        if avg > cls.BULLISH_THRESHOLD:
            return "BULLISH"
        elif avg < cls.BEARISH_THRESHOLD:
            return "BEARISH"
        return "NEUTRAL"
