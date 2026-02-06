"""
src/quantum_edge_core/strategies/router.py

Strategy Router: Regime Switching Logic.
"""

import structlog
from typing import Optional, Any

from quantum_edge_core.strategies.base import BaseStrategy, TradeSignal
from quantum_edge_core.strategies.mean_reversion import MeanReversionStrategy
from quantum_edge_core.strategies.whale_follower import WhaleFollowerStrategy
from quantum_edge_core.events import MarketMetrics, MarketTrade

logger = structlog.get_logger()

class StrategyRouter:
    def __init__(self):
        self.logger = logger.bind(component="StrategyRouter")
        
        # Strategies
        self.mean_reversion = MeanReversionStrategy()
        self.whale_follower = WhaleFollowerStrategy()
        
        # State
        self.current_regime = "RANGE" # Default
        self.active_strategy: BaseStrategy = self.mean_reversion
        
    def on_metrics(self, metrics: MarketMetrics):
        """
        Update Regime and switch active strategy.
        """
        if metrics.regime != self.current_regime:
            self.logger.info("Regime Change Detected", old=self.current_regime, new=metrics.regime)
            self.current_regime = metrics.regime
            
            # Logic
            if "TREND" in self.current_regime or "VOLATILE" in self.current_regime:
                self.logger.info("Switching to WhaleFollower (Trend/Volatile)")
                self.active_strategy = self.whale_follower
            else:
                self.logger.info("Switching to MeanReversion (Range)")
                self.active_strategy = self.mean_reversion

    async def on_trade(self, event: Any) -> Optional[TradeSignal]:
        """
        Delegate trade event to active strategy.
        """
        # Pass to active strategy
        return await self.active_strategy.on_trade(event)
