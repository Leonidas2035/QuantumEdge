"""
Position Manager.
Tracks the virtual state of the bot's position (Inventory).
Responsible for PnL tracking and local state updates before Exchange confirmation.
"""
from dataclasses import dataclass

@dataclass
class PositionState:
    avg_price: float = 0.0
    total_qty: float = 0.0
    unrealized_pnl: float = 0.0

class PositionManager:
    """
    Manages the internal position state.
    """
    def __init__(self):
        self.state = PositionState()

    def simulate_fill(self, price: float, qty: float, side: str):
        """
        Updates the position state assuming a fill occurred.
        
        Args:
            price: Fill price.
            qty: Fill quantity (always positive).
            side: 'BUY' or 'SELL'.
        """
        if side == 'BUY':
            # Weighted Average Price logic
            total_cost = (self.state.avg_price * self.state.total_qty) + (price * qty)
            new_qty = self.state.total_qty + qty
            
            self.state.avg_price = total_cost / new_qty if new_qty > 0 else 0.0
            self.state.total_qty = new_qty
            
        elif side == 'SELL':
            # FIFO / Partial Reduction (Avg Entry usually doesn't change on reduction unless LIFO specified)
            # Standard accounting: Selling reduces quantity but keeps Avg Entry Price same.
            # Realized PnL is captured elsewhere.
            
            # Note: If we flip short, this logic needs expansion. 
            # Prompt assumes Long-only DCA logic ("If LONG ... Signal SELL").
            
            new_qty = max(0.0, self.state.total_qty - qty)
            if new_qty == 0:
                self.state.avg_price = 0.0
            
            self.state.total_qty = new_qty
            
        elif side == 'HEDGE_SHORT':
            # In a Hedged state, we might effectively zero out delta.
            # For this simple manager, we assume separate tracking or net delta.
            # If we open a short hedge, we are effectively neutral.
            # Let's treat 'HEDGE_SHORT' as effectively reducing delta to 0 or negative.
            # Since logic says "OPEN_SHORT (Hedge)", we might be tracking net delta.
            # For now, let's just track it as a special state or ignore effect on 'Long' Avg Price.
            pass

    def get_drawdown_pct(self, current_price: float) -> float:
        """
        Calculates negative drift percentage from average entry.
        Returns positive float for drawdown (e.g. 0.02 for -2%).
        """
        if self.state.total_qty == 0 or self.state.avg_price == 0:
            return 0.0
            
        # Drawdown = (Entry - Current) / Entry
        # If Price is higher (Profit), result is negative.
        # If Price is lower (Loss), result is positive.
        
        raw_diff = (self.state.avg_price - current_price) / self.state.avg_price
        return max(0.0, raw_diff)

    @property
    def total_qty(self) -> float:
        return self.state.total_qty

    @property
    def avg_price(self) -> float:
        return self.state.avg_price
