"""Local Order Book Manager — Maintains L2 book from Binance depth deltas.

Processes incremental depth updates (``@depth@100ms``) to maintain a
sorted local order book.  Provides ``get_snapshot_with_walls()`` for
whale wall detection at configurable depth and BTC threshold.
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("OrderBookManager")


class OrderBookManager:
    """Maintains a local L2 order book from Binance depth deltas.

    Uses sorted dicts (price → qty) for O(log n) insert and O(1) lookup.
    Levels with qty=0.0 are automatically pruned.

    Parameters
    ----------
    symbol : str
        Trading pair, e.g. "BTCUSDT".
    max_levels : int
        Maximum levels to retain per side (memory bound).
    """

    def __init__(self, symbol: str = "BTCUSDT", max_levels: int = 500):
        self.symbol = symbol
        self.max_levels = max_levels
        # price (float) → qty (float)
        self._bids: Dict[float, float] = {}
        self._asks: Dict[float, float] = {}
        self._last_update_id: int = 0
        self._initialized: bool = False

    def apply_delta(
        self,
        bids_delta: List[List],
        asks_delta: List[List],
        final_update_id: int = 0,
    ) -> None:
        """Apply incremental depth update to local book.

        Parameters
        ----------
        bids_delta : list of [price_str, qty_str]
            Bid level updates. qty=0 means remove level.
        asks_delta : list of [price_str, qty_str]
            Ask level updates. qty=0 means remove level.
        final_update_id : int
            Binance ``u`` field for sequencing.
        """
        # Apply bid deltas
        for level in bids_delta:
            if len(level) < 2:
                continue
            price = float(level[0])
            qty = float(level[1])
            if qty <= 0.0:
                self._bids.pop(price, None)
            else:
                self._bids[price] = qty

        # Apply ask deltas
        for level in asks_delta:
            if len(level) < 2:
                continue
            price = float(level[0])
            qty = float(level[1])
            if qty <= 0.0:
                self._asks.pop(price, None)
            else:
                self._asks[price] = qty

        # Prune to max_levels (keep best prices)
        if len(self._bids) > self.max_levels:
            sorted_bids = sorted(self._bids.keys(), reverse=True)
            for price in sorted_bids[self.max_levels:]:
                del self._bids[price]

        if len(self._asks) > self.max_levels:
            sorted_asks = sorted(self._asks.keys())
            for price in sorted_asks[self.max_levels:]:
                del self._asks[price]

        if final_update_id:
            self._last_update_id = final_update_id
        self._initialized = True

    def get_snapshot(
        self,
        depth: int = 20,
        wall_threshold: float = 2.0,
    ) -> dict:
        """Сортує стакан, знаходить стіни і повертає дані.

        Parameters
        ----------
        depth : int
            Number of price levels per side.
        wall_threshold : float
            Minimum BTC qty for a level to be a "whale wall".

        Returns
        -------
        dict
            {"bids": [...], "asks": [...], "whale_walls": [{"side", "price", "quantity"}, ...]}
        """
        # Sort: bids descending (best first), asks ascending (best first)
        sorted_bids = sorted(self._bids.items(), reverse=True)[:depth]
        sorted_asks = sorted(self._asks.items())[:depth]

        top_bids = [[p, q] for p, q in sorted_bids]
        top_asks = [[p, q] for p, q in sorted_asks]

        # Detect whale walls
        whale_walls = []
        for price, qty in sorted_bids:
            if qty >= wall_threshold:
                whale_walls.append({"side": "BID", "price": price, "quantity": qty})
        for price, qty in sorted_asks:
            if qty >= wall_threshold:
                whale_walls.append({"side": "ASK", "price": price, "quantity": qty})

        return {
            "bids": top_bids,
            "asks": top_asks,
            "whale_walls": whale_walls
        }

    @property
    def mid_price(self) -> float:
        """Current mid-price from best bid/ask."""
        if not self._bids or not self._asks:
            return 0.0
        best_bid = max(self._bids.keys())
        best_ask = min(self._asks.keys())
        return (best_bid + best_ask) / 2.0

    @property
    def spread(self) -> float:
        """Current spread."""
        if not self._bids or not self._asks:
            return 0.0
        return min(self._asks.keys()) - max(self._bids.keys())

    @property
    def is_ready(self) -> bool:
        """Whether the book has received at least one delta."""
        return self._initialized and bool(self._bids) and bool(self._asks)

    @property
    def stats(self) -> Dict[str, Any]:
        """Diagnostic stats."""
        return {
            "symbol": self.symbol,
            "bid_levels": len(self._bids),
            "ask_levels": len(self._asks),
            "mid_price": self.mid_price,
            "spread": self.spread,
            "last_update_id": self._last_update_id,
        }
