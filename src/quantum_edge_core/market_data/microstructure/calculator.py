"""
L2 Microstructure Engine Calculator.
Calculates Order Flow Imbalance (OFI), Cumulative Volume Delta (CVD),
Bid/Ask Imbalance, and Whale Walls.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Dict, List, Optional, Any


class MicrostructureCalculator:
    """
    Highly-optimized microstructure calculator keeping track of Order Book and Trades.
    """

    def __init__(
        self,
        ofi_window_sec: float = 1.0,
        cvd_window_sec: float = 10.0,
        whale_wall_threshold: float = 10.0,
    ) -> None:
        self.ofi_window_ns = int(ofi_window_sec * 1_000_000_000)
        self.cvd_window_ns = int(cvd_window_sec * 1_000_000_000)
        self.whale_wall_threshold = whale_wall_threshold

        # OFI calculation state
        self.prev_best_bid: Optional[float] = None
        self.prev_best_bid_qty: Optional[float] = None
        self.prev_best_ask: Optional[float] = None
        self.prev_best_ask_qty: Optional[float] = None

        # Deque for tick-to-tick raw OFI values: stores tuple of (timestamp_ns, ofi_value)
        self._ofi_ticks: deque[tuple[int, float]] = deque()

        # Deque for trade volumes: stores tuple of (timestamp_ns, buy_vol, sell_vol)
        self._trade_ticks: deque[tuple[int, float, float]] = deque()

    def mark_resync(self) -> None:
        """No-op resync marker for aggregator compatibility."""
        return

    def update_trade(self, ts_ns: int, price: float, size: float, side: str) -> None:
        """
        Record a trade event to update the Cumulative Volume Delta (CVD).
        side: 'buy' or 'sell' (case-insensitive)
        """
        size = float(size)
        if size <= 0:
            return

        side_upper = str(side).upper()
        buy_vol = size if side_upper in ("BUY", "1") else 0.0
        sell_vol = size if side_upper in ("SELL", "0") else 0.0

        self._trade_ticks.append((ts_ns, buy_vol, sell_vol))
        self._drain_trades(ts_ns)

    def _drain_trades(self, current_ts_ns: int) -> None:
        cutoff = current_ts_ns - self.cvd_window_ns
        while self._trade_ticks and self._trade_ticks[0][0] < cutoff:
            self._trade_ticks.popleft()

    def _drain_ofi(self, current_ts_ns: int) -> None:
        cutoff = current_ts_ns - self.ofi_window_ns
        while self._ofi_ticks and self._ofi_ticks[0][0] < cutoff:
            self._ofi_ticks.popleft()

    def update_book(
        self,
        symbol: str,
        bids: List[List[float]],
        asks: List[List[float]],
        ts_ns: int,
    ) -> Dict[str, Any]:
        """
        Record an order book depth update to compute L2 metrics.
        bids: list of [price, qty] levels
        asks: list of [price, qty] levels
        """
        # 1. OFI Calculation
        ofi_val = 0.0
        if bids and asks:
            try:
                best_bid = float(bids[0][0])
                best_bid_qty = float(bids[0][1])
                best_ask = float(asks[0][0])
                best_ask_qty = float(asks[0][1])

                if self.prev_best_bid is not None and self.prev_best_ask is not None:
                    # dBidQty
                    if best_bid > self.prev_best_bid:
                        d_bid = best_bid_qty
                    elif best_bid < self.prev_best_bid:
                        d_bid = -self.prev_best_bid_qty
                    else:
                        d_bid = best_bid_qty - self.prev_best_bid_qty

                    # dAskQty
                    if best_ask < self.prev_best_ask:
                        d_ask = best_ask_qty
                    elif best_ask > self.prev_best_ask:
                        d_ask = -self.prev_best_ask_qty
                    else:
                        d_ask = best_ask_qty - self.prev_best_ask_qty

                    ofi_val = d_bid - d_ask

                self.prev_best_bid = best_bid
                self.prev_best_bid_qty = best_bid_qty
                self.prev_best_ask = best_ask
                self.prev_best_ask_qty = best_ask_qty
            except (ValueError, IndexError):
                pass

        # Append to OFI ticks and drain
        self._ofi_ticks.append((ts_ns, ofi_val))
        self._drain_ofi(ts_ns)

        # Sum of OFI ticks over the last 1s
        ofi_1s = sum(tick[1] for tick in self._ofi_ticks)

        # 2. CVD Calculation over last 10s
        self._drain_trades(ts_ns)
        total_buy_vol = sum(tick[1] for tick in self._trade_ticks)
        total_sell_vol = sum(tick[2] for tick in self._trade_ticks)
        cvd_10s = total_buy_vol - total_sell_vol

        # 3. Imbalance top 10 levels
        imbalance_top10 = 0.0
        bid_vol_sum = sum(float(level[1]) for level in bids[:10] if len(level) >= 2)
        ask_vol_sum = sum(float(level[1]) for level in asks[:10] if len(level) >= 2)
        total_vol = bid_vol_sum + ask_vol_sum
        if total_vol > 0:
            imbalance_top10 = (bid_vol_sum - ask_vol_sum) / total_vol

        # 4. Whale Walls: Any price level in the Top 50 where qty >= 10.0 BTC (threshold)
        whale_walls = []
        for side, levels in [("BID", bids), ("ASK", asks)]:
            for level in levels[:50]:
                try:
                    price = float(level[0])
                    qty = float(level[1])
                    if qty >= self.whale_wall_threshold:
                        whale_walls.append({
                            "price": price,
                            "qty": qty,
                            "side": side
                        })
                except (ValueError, IndexError):
                    continue

        return {
            "ofi_1s": ofi_1s,
            "cvd_10s": cvd_10s,
            "imbalance_top10": imbalance_top10,
            "whale_walls": whale_walls
        }
