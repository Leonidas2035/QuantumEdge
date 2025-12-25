from dataclasses import dataclass
from typing import Optional, Any


@dataclass
class OrderRequest:
    symbol: str
    side: str
    position_side: str
    order_type: str
    qty: float
    price: Optional[float] = None
    reduce_only: bool = False
    client_order_id: Optional[str] = None
    time_in_force: Optional[str] = None


@dataclass
class OrderResult:
    order_id: str
    client_order_id: Optional[str]
    status: str
    filled_qty: float
    avg_price: float
    raw: Any


@dataclass
class Position:
    symbol: str
    position_side: str
    qty: float
    entry_price: float
    unrealized_pnl: float
    leverage: float
    liquidation_price: float
    raw: Any


@dataclass
class Balance:
    asset: str
    available: float
    total: float
    raw: Any
