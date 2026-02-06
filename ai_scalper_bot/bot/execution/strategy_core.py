from enum import Enum, auto
from dataclasses import dataclass
from typing import Optional
from ..core.models import MarketState

class BotState(Enum):
    IDLE = auto()
    LONG_ACCUMULATION = auto()
    HEDGED = auto()

@dataclass
class TradeAction:
    action_type: str  # 'BUY', 'SELL'
    price: float
    qty: float
    reason: str

class AdaptiveGridStrategy:
    """Логіка прийняття рішень на основі волатильності."""
    def __init__(self):
        self.state = BotState.IDLE
        self.position_qty = 0.0
        self.last_action_time = 0

    def decide(self, market: MarketState, ofi: float, vpin: float) -> Optional[TradeAction]:
        # Проста логіка для тесту
        if self.state == BotState.IDLE:
            # Сигнал на покупку: OFI позитивний (тиск покупців)
            if ofi > 0.1:
                self.state = BotState.LONG_ACCUMULATION
                return TradeAction('BUY', market.best_ask, 0.001, f"OFI Breakout {ofi:.2f}")
        
        elif self.state == BotState.LONG_ACCUMULATION:
            # Сигнал на продаж (Take Profit)
            if ofi < -0.1:
                self.state = BotState.IDLE
                return TradeAction('SELL', market.best_bid, 0.001, "OFI Reversal")
                
        return None
