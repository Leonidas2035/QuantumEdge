"""OrderBook Aggregator — Whale Wall Detection.

Scans raw L2 order book data (bids/asks) to detect large
liquidity walls (≥ configurable BTC threshold) within a
configurable depth window (default ±2% from current price).

Output:
    {
        'closest_bid_wall_btc': 25.4,
        'closest_bid_wall_dist_pct': -0.5,
        'closest_ask_wall_btc': 45.0,
        'closest_ask_wall_dist_pct': 0.2
    }
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("OrderBookAggregator")


@dataclass
class WallInfo:
    """Detected liquidity wall."""

    price: float
    size_btc: float
    distance_pct: float
    side: str  # "BUY" or "SELL"


@dataclass
class OrderBookAggregator:
    """Whale Wall detector for L2 order book snapshots.

    Parameters
    ----------
    wall_threshold_btc : float
        Minimum BTC size for a level to be considered a "wall".
    max_depth_pct : float
        Maximum distance (%) from current price to scan.
    """

    wall_threshold_btc: float = 20.0
    max_depth_pct: float = 2.0
    _last_walls: Dict[str, Any] = field(default_factory=dict, repr=False)

    def process_book(
        self,
        bids: List[List[float]],
        asks: List[List[float]],
        current_price: float,
    ) -> Dict[str, Any]:
        """Scan bids/asks and detect closest whale walls.

        Parameters
        ----------
        bids : list of [price, qty]
            Bid levels sorted by price descending (best bid first).
        asks : list of [price, qty]
            Ask levels sorted by price ascending (best ask first).
        current_price : float
            Current market price (mid or last trade).

        Returns
        -------
        dict with keys:
            closest_bid_wall_btc, closest_bid_wall_dist_pct,
            closest_ask_wall_btc, closest_ask_wall_dist_pct
        """
        if current_price <= 0:
            return self._empty_result()

        bid_wall = self._find_wall(bids, current_price, side="BUY")
        ask_wall = self._find_wall(asks, current_price, side="SELL")

        result = {
            "closest_bid_wall_btc": bid_wall.size_btc if bid_wall else 0.0,
            "closest_bid_wall_dist_pct": bid_wall.distance_pct if bid_wall else 0.0,
            "closest_ask_wall_btc": ask_wall.size_btc if ask_wall else 0.0,
            "closest_ask_wall_dist_pct": ask_wall.distance_pct if ask_wall else 0.0,
        }

        self._last_walls = result
        return result

    def _find_wall(
        self,
        levels: List[List[float]],
        current_price: float,
        side: str,
    ) -> Optional[WallInfo]:
        """Find the closest level with cumulative size ≥ threshold.

        Scans levels within max_depth_pct of current_price.
        For each level, checks if a single level's qty ≥ threshold.
        Also accumulates a running total to detect distributed walls.
        """
        cumulative_qty = 0.0
        max_depth_boundary = current_price * (self.max_depth_pct / 100.0)

        for idx, level in enumerate(levels):
            if idx >= len(levels):
                break

            if len(level) < 2:
                continue

            price, qty = float(level[0]), float(level[1])

            # Distance from current price
            dist_pct = ((price - current_price) / current_price) * 100.0

            # Check depth boundary (±max_depth_pct)
            if abs(dist_pct) > self.max_depth_pct:
                break  # Beyond scan range

            # Single-level wall detection
            if qty >= self.wall_threshold_btc:
                return WallInfo(
                    price=price,
                    size_btc=qty,
                    distance_pct=round(dist_pct, 4),
                    side=side,
                )

            # Cumulative wall detection
            cumulative_qty += qty
            if cumulative_qty >= self.wall_threshold_btc:
                return WallInfo(
                    price=price,
                    size_btc=round(cumulative_qty, 4),
                    distance_pct=round(dist_pct, 4),
                    side=side,
                )

        return None

    @property
    def last_walls(self) -> Dict[str, Any]:
        """Return the last computed wall features."""
        return self._last_walls if self._last_walls else self._empty_result()

    @staticmethod
    def _empty_result() -> Dict[str, Any]:
        return {
            "closest_bid_wall_btc": 0.0,
            "closest_bid_wall_dist_pct": 0.0,
            "closest_ask_wall_btc": 0.0,
            "closest_ask_wall_dist_pct": 0.0,
        }
