"""
src/quantum_edge_core/strategies/whale_follower.py

Whale Follower Strategy.
"""

import time
import structlog
from typing import Optional, Any

from quantum_edge_core.strategies.base import BaseStrategy, TradeSignal
from quantum_edge_core.events import LargeBlockEvent

logger = structlog.get_logger()


class WhaleFollowerStrategy(BaseStrategy):
    def __init__(self):
        self.logger = logger.bind(strategy="WhaleFollower")

    async def on_trade(self, event: Any) -> Optional[TradeSignal]:
        # We process both MarketTrade (for context) and LargeBlockEvents
        if isinstance(event, LargeBlockEvent):
            self.logger.info(
                "WHALE ALERT RECEIVED", quantity=event.quantity, side=event.side
            )

            # Simple Logic: Follow the whale
            # In production, we'd check VWAP/Liquidity before following.
            return TradeSignal(
                symbol=event.symbol,
                side=event.side,
                quantity=0.05,  # Aggressive size
                reason=f"Whale Following ({event.quantity} BTC)",
                timestamp=time.time(),
            )

        return None
