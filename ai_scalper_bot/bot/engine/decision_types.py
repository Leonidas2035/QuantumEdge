from dataclasses import dataclass, field
from typing import Dict, List, Optional


class DecisionDirection:
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"
    NONE = "none"


class DecisionAction:
    ENTER = "enter"
    EXIT = "exit"
    HOLD = "hold"
    NO_TRADE = "no_trade"


@dataclass
class HorizonDecision:
    horizon: int
    direction: str  # long/short/none
    confidence: float
    edge: float


@dataclass
class Decision:
    action: str  # enter/exit/hold/no_trade
    direction: str = DecisionDirection.NONE
    size: float = 0.0
    confidence: float = 0.0
    edge: float = 0.0
    regime: Optional[str] = None
    trade_style: Optional[str] = None
    horizons_used: List[int] = field(default_factory=list)
    horizon_details: Dict[int, HorizonDecision] = field(default_factory=dict)
    reasons: List[str] = field(default_factory=list)
    order_type: str = "market"
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
