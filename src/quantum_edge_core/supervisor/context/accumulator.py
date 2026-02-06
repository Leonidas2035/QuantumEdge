"""
Market Accumulator.
Stores recent market data (trades, candles) in efficient ring buffers.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Any, Optional
import time

@dataclass
class Trade:
    price: float
    quantity: float
    side: str # "buy" or "sell"
    timestamp: float

@dataclass
class Candle:
    ts: float
    open: float
    high: float
    low: float
    close: float
    volume: float

class MarketAccumulator:
    """
    In-memory window of recent market events.
    Window sizes determined by technical constraints (approx 1000 trades, 60 candles).
    """
    def __init__(self, trade_window: int = 1000, candle_window: int = 60):
        self._trades: Deque[Trade] = deque(maxlen=trade_window)
        self._candles: Deque[Candle] = deque(maxlen=candle_window)
        
        # Latest L2 Snapshot (simplified)
        self._book_snapshot: Dict[str, Any] = {}
        self.last_update_time: float = 0.0

    def add_trade(self, trade_msg: Dict[str, Any]):
        """
        Append a new trade.
        Expected msg format: {'p': float, 'q': float, 'm': bool (maker side), 'T': ts}
        """
        try:
            # Normalize binance/standard format
            # assuming 'p' is price, 'q' is quantity, 'm' being true means maker was buy? 
            # Or simplified input: {"price": ..., "qty": ..., "side": ...}
            
            # Let's handle a generic format or standardized format
            price = float(trade_msg.get("price", trade_msg.get("p", 0.0)))
            qty = float(trade_msg.get("quantity", trade_msg.get("q", 0.0)))
            side = trade_msg.get("side")
            
            if not side and "m" in trade_msg:
                 # If 'm' is True (Buyer is Maker) -> Seller is Taker -> Side = Sell
                 # If 'm' is False (Seller is Maker) -> Buyer is Taker -> Side = Buy
                 side = "sell" if trade_msg["m"] else "buy"
            
            ts = trade_msg.get("timestamp", trade_msg.get("T", time.time() * 1000))
            
            t = Trade(price, qty, str(side), ts)
            self._trades.append(t)
            self.last_update_time = time.time()
            
        except (ValueError, KeyError):
            pass

    def add_candle(self, candle_msg: Dict[str, Any]):
         """Add a 1m candle update."""
         try:
             c = Candle(
                 ts=candle_msg.get("t", 0),
                 open=float(candle_msg.get("o", 0)),
                 high=float(candle_msg.get("h", 0)),
                 low=float(candle_msg.get("l", 0)),
                 close=float(candle_msg.get("c", 0)),
                 volume=float(candle_msg.get("v", 0))
             )
             self._candles.append(c)
         except Exception:
             pass

    def add_book_snapshot(self, book_msg: Dict[str, Any]):
        """Store latest L2 state."""
        # book_msg expected: {"bids": [[price, qty], ...], "asks": ...}
        self._book_snapshot = book_msg
        self.last_update_time = time.time()

    @property
    def trades(self) -> Deque[Trade]:
        return self._trades

    @property
    def candles(self) -> Deque[Candle]:
        return self._candles

    @property
    def order_book(self) -> Dict[str, Any]:
        return self._book_snapshot
