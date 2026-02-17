"""
Feature Facade.
Centralizes the calculation of all microstructure alpha indicators.
"""

import logging
from dataclasses import dataclass

from quantum_edge_core.ai_scalper_bot.bot.core.models import MarketState, MarketTick
from quantum_edge_core.ai_scalper_bot.bot.features.ofi import OfiCalculator
from quantum_edge_core.ai_scalper_bot.bot.features.vpin import VpinCalculator

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class FeatureVector:
    """
    Clean feature vector ready for strategy logic.
    """

    timestamp: float
    ofi: float
    vpin: float
    # Future features: spread, depth_ratio, etc.


class FeatureEngine:
    """
    Orchestrates updating of all alpha indicators.
    """

    def __init__(self, vpin_bucket_vol: float = 1.0, vpin_window: int = 50):
        self.ofi_calc = OfiCalculator()
        self.vpin_calc = VpinCalculator(bucket_vol=vpin_bucket_vol, window=vpin_window)

        # State
        self.latest_ofi: float = 0.0
        self.latest_vpin: float = 0.0

    def update(self, tick: MarketTick, book_state: MarketState) -> FeatureVector:
        """
        Updates all indicators based on new market data.

        Args:
            tick: The latest trade.
            book_state: The current state of the orderbook (Snapshot).

        Returns:
            FeatureVector containing current alpha values.
        """
        # 1. Update OFI
        # OFI depends on Book State changes
        try:
            val = self.ofi_calc.update(book_state)
            self.latest_ofi = val  # OFI is instantaneous flow
        except Exception as e:
            logger.error(f"OFI Calc Error: {e}")

        # 2. Update VPIN
        # VPIN depends on Trade Volume
        try:
            v_val = self.vpin_calc.update(tick)
            if v_val is not None:
                self.latest_vpin = v_val
        except Exception as e:
            logger.error(f"VPIN Calc Error: {e}")

        return FeatureVector(
            timestamp=book_state.timestamp, ofi=self.latest_ofi, vpin=self.latest_vpin
        )
