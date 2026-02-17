"""
Adaptive Grid Strategy Core.
Decides trade actions based on Market State, Alpha Features, and Position Risk.
"""

from enum import Enum, auto
from dataclasses import dataclass
from typing import Optional, Dict, Any

from quantum_edge_core.ai_scalper_bot.bot.core.models import MarketState
from quantum_edge_core.ai_scalper_bot.bot.features.facade import FeatureVector
from quantum_edge_core.ai_scalper_bot.bot.execution.position import PositionManager


class BotState(Enum):
    IDLE = auto()
    LONG_ACCUMULATION = auto()
    HEDGED = auto()


@dataclass
class TradeAction:
    action_type: str  # 'BUY', 'SELL', 'HEDGE_SHORT'
    price: float
    qty: float
    reason: str


class AdaptiveGridStrategy:
    """
    Decides WHEN to trade based on Market State, Indicators, and Risk.
    Adapts grid spacing using ATR (Volatility).
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Args:
            config: Strategy configuration dictionary.
                    Required: 'base_order_size_q', 'safety_order_multiplier',
                              'hedge_trigger_dd', 'grid_step_atr_mult', 'take_profit_pct', 'ofi_entry_threshold'.
        """
        self.state = BotState.IDLE
        self.config = config
        self.last_buy_price: float = 0.0

    def decide(
        self,
        market: MarketState,
        features: FeatureVector,
        atr: float,
        position: PositionManager,
    ) -> Optional[TradeAction]:
        """
        Main Decision Function.
        """
        current_price = market.last_price  # Or best_bid? Using last_price as reference.

        # 0. State Transition Check
        if position.total_qty == 0 and self.state == BotState.LONG_ACCUMULATION:
            self.state = BotState.IDLE
        elif position.total_qty > 0 and self.state == BotState.IDLE:
            self.state = BotState.LONG_ACCUMULATION

        # 1. Check for Hedge Trigger (Critical Safety)
        if position.total_qty > 0 and self.state != BotState.HEDGED:
            dd = position.get_drawdown_pct(current_price)
            if dd > self.config.get("hedge_trigger_dd", 0.02):
                self.state = BotState.HEDGED
                # Hedge full quantity to be Delta Neutral
                return TradeAction(
                    action_type="HEDGE_SHORT",
                    price=market.best_bid,  # Sell into bid
                    qty=position.total_qty,
                    reason="Risk Limit Reached: Drawdown > Threshold",
                )

        # 2. Logic based on States
        if self.state == BotState.IDLE:
            # Entry Logic: High OFI (Buying Pressure)
            if features.ofi > self.config.get("ofi_entry_threshold", 0.1):
                qty = self.config.get("base_order_size_q", 0.01)
                self.last_buy_price = (
                    current_price  # Temporarily set, confirmed on fill
                )
                return TradeAction(
                    action_type="BUY",
                    price=market.best_ask,  # Buy from ask
                    qty=qty,
                    reason=f"High OFI ({features.ofi:.2f})",
                )

        elif self.state == BotState.LONG_ACCUMULATION:
            # A. Take Profit Check
            tp_price = position.avg_price * (
                1 + self.config.get("take_profit_pct", 0.01)
            )
            if current_price >= tp_price:
                return TradeAction(
                    action_type="SELL",
                    price=market.best_bid,
                    qty=position.total_qty,
                    reason="Take Profit Hit",
                )

            # B. DCA Check (Dynamic ATR Spacing)
            # If price < last_buy - (ATR * gap_mult)
            gap = atr * self.config.get("grid_step_atr_mult", 2.0)

            # Safety: Ensure Gap is not zero or tiny
            if gap < current_price * 0.0005:  # Minimum 5 bps spacing
                gap = current_price * 0.0005

            if current_price <= (self.last_buy_price - gap):
                # DCA Size: Multiplier * Base (Simplified)
                # In real bot, we'd replicate existing size or use martingale
                dca_qty = self.config.get("base_order_size_q", 0.01)  # Simplified

                self.last_buy_price = current_price
                return TradeAction(
                    action_type="BUY",
                    price=market.best_ask,
                    qty=dca_qty,
                    reason=f"DCA Step (ATR Gap: {gap:.2f})",
                )

        elif self.state == BotState.HEDGED:
            # Logic to unwind hedge would go here.
            # For now, stay hedged until manual intervention or further logic.
            pass

        return None
