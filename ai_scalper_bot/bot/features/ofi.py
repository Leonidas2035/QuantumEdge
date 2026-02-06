from ..core.models import MarketState

class OfiCalculator:
    """Order Flow Imbalance (Cont et al.)"""
    def __init__(self):
        self._prev: MarketState = None

    def update(self, curr: MarketState) -> float:
        if self._prev is None:
            self._prev = curr
            return 0.0
        
        # Bid Side
        if curr.best_bid > self._prev.best_bid:
            e_bid = curr.best_bid_qty
        elif curr.best_bid < self._prev.best_bid:
            e_bid = -self._prev.best_bid_qty
        else:
            e_bid = curr.best_bid_qty - self._prev.best_bid_qty
            
        # Ask Side
        if curr.best_ask > self._prev.best_ask:
            e_ask = -self._prev.best_ask_qty
        elif curr.best_ask < self._prev.best_ask:
            e_ask = curr.best_ask_qty
        else:
            e_ask = curr.best_ask_qty - self._prev.best_ask_qty

        self._prev = curr
        return e_bid - e_ask
