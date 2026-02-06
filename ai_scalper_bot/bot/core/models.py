from dataclasses import dataclass, field
from typing import List, Optional

@dataclass(slots=True)
class MarketTick:
    """Нормалізований тік з ринку."""
    price: float
    quantity: float
    timestamp: int  # ms
    is_buyer_maker: bool
    symbol: str

@dataclass(slots=True)
class MarketState:
    """Зріз ринку (Snapshot) для прийняття рішень."""
    timestamp: float
    best_bid: float
    best_bid_qty: float
    best_ask: float
    best_ask_qty: float
    last_price: float
