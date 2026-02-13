"""
Liquidation Heatmap Engine.
Aggregates liquidation events into price buckets to identify high-density clusters.
Uses a time-based sliding window (default 15m) to keep data relevant.
"""

from __future__ import annotations

import time
from collections import deque, defaultdict
from typing import Dict, List, Any, Deque, Tuple
import logging

logger = logging.getLogger(__name__)


class LiquidationHeatmap:
    """
    Real-time Heatmap for Liquidation Events.
    Maintains a rolling window of volume per price bucket.
    """

    def __init__(self, bin_size: float = 10.0, retention_window_s: int = 900):
        self.bin_size = bin_size  # e.g. 10.0 for BTC
        self.retention_window_s = retention_window_s  # 15 minutes

        # Aggregated State: bucket_price -> {'buy': vol, 'sell': vol}
        # We track separate sides to determine the "dominant" side of a cluster
        self.clusters: Dict[float, Dict[str, float]] = defaultdict(lambda: {"buy": 0.0, "sell": 0.0})

        # Rolling Window History: (timestamp_ns, bucket_price, side, volume)
        # Used for efficient decrementing/pruning without re-scanning everything
        self.history: Deque[Tuple[int, float, str, float]] = deque()

        self.last_update_ts = 0

    def on_liquidation(self, event: Dict[str, Any]) -> None:
        """
        Ingest a liquidation event.
        Expects keys: 'price', 'usd_size', 'side', 'timestamp' (ms) or 'received_at' (ns)
        """
        try:
            price = float(event.get("price", 0.0))
            usd_size = float(event.get("usd_size", 0.0))
            side = event.get("side", "UNKNOWN").upper()  # BUY or SELL

            # Use received_at (ns) for internal retention tracking if available, else convert ms
            ts_ns = event.get("received_at") or (int(event.get("timestamp", time.time() * 1000)) * 1_000_000)

            if price <= 0 or usd_size <= 0:
                return

            # 1. Determine Bucket
            # Round to nearest bin_size
            bucket = round(price / self.bin_size) * self.bin_size

            # 2. Add to State
            self.clusters[bucket][side.lower()] += usd_size

            # 3. Add to History
            self.history.append((ts_ns, bucket, side.lower(), usd_size))

            self.last_update_ts = ts_ns

        except Exception as e:
            logger.error(f"Error updating heatmap: {e}")

    def prune(self, current_ts_ns: int = None) -> None:
        """
        Remove events older than retention window.
        """
        if current_ts_ns is None:
            current_ts_ns = time.time_ns()

        cutoff_ns = current_ts_ns - (self.retention_window_s * 1_000_000_000)

        while self.history:
            # Peek oldest
            ts, bucket, side, vol = self.history[0]

            if ts < cutoff_ns:
                # Remove from State
                self.clusters[bucket][side] -= vol

                # Cleanup empty/near-zero entries to keep dict small
                if self.clusters[bucket][side] <= 1.0:  # Floating point epsilon safety
                    self.clusters[bucket][side] = 0.0

                # If both sides are empty, remove bucket key
                if self.clusters[bucket]["buy"] <= 1.0 and self.clusters[bucket]["sell"] <= 1.0:
                    del self.clusters[bucket]

                # Pop from history
                self.history.popleft()
            else:
                break

    def get_top_clusters(self, n: int = 3) -> List[Dict[str, Any]]:
        """
        Return top N active liquidation clusters sorted by total volume.
        """
        # Prune first to ensure accuracy
        self.prune()

        # Convert clusters to list of dicts for sorting
        # Calculate Total Volume per bucket
        candidates = []
        for price, sides in self.clusters.items():
            buy_vol = sides["buy"]
            sell_vol = sides["sell"]
            total_vol = buy_vol + sell_vol

            if total_vol < 1.0:
                continue

            # Determine Dominant Side
            dominant_side = "BUY" if buy_vol > sell_vol else "SELL"

            candidates.append({"price": price, "vol": total_vol, "bias": dominant_side})

        # Sort by volume desc
        candidates.sort(key=lambda x: x["vol"], reverse=True)

        return candidates[:n]

    @property
    def total_volume(self) -> float:
        """Total volume in current window."""
        return sum(c["vol"] for c in self.get_top_clusters(n=10000))  # Simple sum scan
