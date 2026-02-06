"""
Microstructure Cache Implementation.
Maintains rolling window of market ticks and current market state.
"""
from collections import deque
from typing import Optional, Dict, Any
import numpy as np

from quantum_edge_core.ai_scalper_bot.bot.core.models import MarketState, MarketTick

class OrderBookCache:
    """
    High-performance in-memory cache for market microstructure data.
    Maintains a rolling window of ticks without Pandas overhead.
    """
    def __init__(self, history_len: int = 1000):
        """
        Args:
            history_len: Maximum number of ticks to keep in history.
        """
        self._history: deque = deque(maxlen=history_len)
        self._current_state: Optional[MarketState] = None
        # Pre-allocate if moving to fixed buffers, but deque is O(1) for append/pop
        
    def update(self, tick: Dict[str, Any]) -> None:
        """
        Updates the internal state based on a new market tick.
        Complexity: O(1)
        
        Args:
            tick: Dictionary containing parsed tick data. 
                  Expected keys: 'p' (price), 'q' (qty), 'T' (acc_time/ts), 'm' (maker).
        """
        try:
            # Fast parsing - assuming dict generic keys from common exchanges (e.g. Binance fmt)
            # Adjust keys based on actual upstream MarketDataHub format if known, 
            # currently deriving from standard 'p', 'q' conventions and User Prompt snippet.
            price = float(tick['p'])
            qty = float(tick['q'])
            ts = float(tick.get('T', 0)) # Default to 0 if missing, or use local time? user says 'T' usually
            is_maker = bool(tick.get('m', False))

            # Update History
            market_tick = MarketTick(
                price=price,
                quantity=qty,
                timestamp=ts,
                is_buyer_maker=is_maker
            )
            self._history.append(market_tick)

            # Update Current State (Snapshot)
            # Assuming the tick also might carry BBO or we just update last_price.
            # Ideally we have B/A from the same update or separate source.
            # If explicit BBO not in tick, we retain previous best_bid/best_ask or set to price (imprecise)
            # For now, updating last_price.
            
            # A real O(1) orderbook update usually requires receiving depth updates.
            # If 'tick' contains BBO:
            best_bid = float(tick.get('b', 0.0))
            best_ask = float(tick.get('a', 0.0))
            best_bid_qty = float(tick.get('B', 0.0))
            best_ask_qty = float(tick.get('A', 0.0))
            
            # If we don't have previous state, init it
            if self._current_state is None:
                self._current_state = MarketState(
                    timestamp=ts,
                    best_bid=best_bid if best_bid else price, # Fallback
                    best_ask=best_ask if best_ask else price, # Fallback
                    best_bid_qty=best_bid_qty,
                    best_ask_qty=best_ask_qty,
                    last_price=price
                )
            else:
                s = self._current_state
                s.timestamp = ts
                s.last_price = price
                if best_bid:
                    s.best_bid = best_bid
                if best_ask:
                    s.best_ask = best_ask
                if best_bid_qty:
                    s.best_bid_qty = best_bid_qty
                if best_ask_qty:
                    s.best_ask_qty = best_ask_qty

        except (KeyError, ValueError, TypeError):
            # Log error strictly or pass as per requirement "pass" in snippet example 
            # (though normally we'd log)
            pass

    def get_snapshot(self) -> np.ndarray:
        """
        Returns a normalized NumPy array ready for the inference engine.
        Format: [[price, qty, is_maker], ...]
        """
        # Converting deque to list then array is O(N), but efficient enough for inference frequency.
        # If strict Microstructure latency is needed for *reading*, we might maintain a circular buffer.
        # However, get_snapshot is usually called by Strategy (Model), decoupled from Tick Ingestion.
        
        if not self._history:
            return np.empty((0, 3), dtype=np.float64)

        # Vectorized construction
        # Mapping objects to array is slower than maintaining array, but 'update' must be O(1).
        # Appending to numpy array is O(N). Deque append is O(1).
        # We accept O(N) cost at snapshot time (Model Inference frequency < Tick Frequency).
        
        # Extract data
        data = [
            [t.price, t.quantity, 1.0 if t.is_buyer_maker else 0.0] 
            for t in self._history
        ]
        
        return np.array(data, dtype=np.float64)
