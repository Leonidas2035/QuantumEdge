"""
src/quantum_edge_core/strategies/base.py

Abstract Base Class for Strategies.
"""

import abc
from typing import Optional

import msgspec

from quantum_edge_core.events import MarketTrade


class TradeSignal(msgspec.Struct):
    symbol: str
    side: str  # "buy" or "sell"
    quantity: float
    reason: str
    timestamp: float


class BaseStrategy(abc.ABC):
    @abc.abstractmethod
    async def on_trade(self, event: MarketTrade) -> Optional[TradeSignal]:
        """
        Process a new trade event and optionally return a Trade Signal.
        """
        pass
