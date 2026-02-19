"""
src/quantum_edge_core/strategies/mean_reversion.py

Simple Mean Reversion Strategy.
"""

import collections
import statistics
import time
from typing import Deque, Optional

import structlog

from quantum_edge_core.events import MarketTrade
from quantum_edge_core.strategies.base import BaseStrategy, TradeSignal

logger = structlog.get_logger()


class MeanReversionStrategy(BaseStrategy):
    def __init__(self, window_size: int = 20, deviation_threshold: float = 0.001):
        self.window_size = window_size
        self.threshold = deviation_threshold
        self.prices: Deque[float] = collections.deque(maxlen=window_size)
        self.logger = logger.bind(strategy="MeanReversion")

    async def on_trade(self, event: MarketTrade) -> Optional[TradeSignal]:
        price = event.price
        self.prices.append(price)

        if len(self.prices) < self.window_size:
            return None

        ma = statistics.mean(self.prices)

        # Logic:
        # Price < MA * (1 - threshold): Oversold -> Buy
        # Price > MA * (1 + threshold): Overbought -> Sell

        if price < ma * (1 - self.threshold):
            self.logger.info("Signal: BUY", price=price, ma=ma, deviation=price / ma)
            return TradeSignal(
                symbol=event.symbol,
                side="buy",
                quantity=0.01,  # Fixed size for demo
                reason="Oversold (Mean Reversion)",
                timestamp=time.time(),
            )
        elif price > ma * (1 + self.threshold):
            self.logger.info("Signal: SELL", price=price, ma=ma, deviation=price / ma)
            return TradeSignal(
                symbol=event.symbol,
                side="sell",
                quantity=0.01,
                reason="Overbought (Mean Reversion)",
                timestamp=time.time(),
            )

        return None
