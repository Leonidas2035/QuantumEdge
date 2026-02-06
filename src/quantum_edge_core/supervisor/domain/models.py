"""
Domain Models for Supervisor Decision Core.
Defines the strict contracts for Risk and Policy.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, Any, Optional, List

# --- Enums ---

class RiskLevel(Enum):
    NORMAL = auto()
    WARNING = auto()   # Reduce exposure, tighten stops
    CRITICAL = auto()  # Liquidation, Halt

class TradingMode(Enum):
    NORMAL = "normal"
    CONSERVATIVE = "conservative" # Reduced leverage, tighter stops
    SNIPER = "sniper"       # High confidence entries only
    WINTER = "winter"       # Reduced activity due to volatility/uncertainty
    FREEZE = "freeze"       # No new trades
    REDUCE_ONLY = "reduce_only" # Only closing trades
    HALT = "halt"           # Hard Stop

# --- Config & State ---

@dataclass
class RiskConfig:
    max_daily_loss: float = 500.0
    max_drawdown_total: float = 1000.0
    max_leverage: float = 20.0
    max_open_orders: int = 10
    max_exposure_notional: float = 50000.0

@dataclass
class PortfolioState:
    equity_start_day: float
    equity_current: float
    unrealized_pnl: float
    total_exposure: float
    open_order_count: int
    used_leverage: float
    
    @property
    def daily_pnl(self) -> float:
        return self.equity_current - self.equity_start_day

# --- Decision Outputs ---

@dataclass
class RiskVerdict:
    level: RiskLevel
    reason: str
    action_required: str  # e.g., "CLOSE_ALL", "CANCEL_ORDERS", "NONE"
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass
class PolicyContract:
    """
    The instruction set sent to the Trading Bot.
    """
    mode: TradingMode
    long_allowed: bool
    short_allowed: bool
    max_leverage: float
    min_order_size: float
    max_position_size: float
    risk_multiplier: float = 1.0
    
    # AI Metadata (Reasons)
    ai_confidence: float = 0.0
    ai_reasoning: str = ""
    
    # Overrides
    close_only: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode.value,
            "long_allowed": self.long_allowed,
            "short_allowed": self.short_allowed,
            "max_leverage": self.max_leverage,
            "min_order_size": self.min_order_size,
            "max_position_size": self.max_position_size,
            "risk_multiplier": self.risk_multiplier,
            "close_only": self.close_only,
            "ai_meta": {
                "confidence": self.ai_confidence,
                "reasoning": self.ai_reasoning
            }
        }
