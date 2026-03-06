import asyncio
import logging
import sys

from quantum_edge_core.ai_scalper_bot.bot.core.models import (
    MarketState,
    TradingMode,
    MarketTick,
)
from quantum_edge_core.ai_scalper_bot.bot.features.facade import FeatureVector
from quantum_edge_core.ai_scalper_bot.bot.execution.position import PositionManager
from quantum_edge_core.ai_scalper_bot.bot.execution.strategy_core import (
    AdaptiveGridStrategy,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("Audit")


class MockMarketState(MarketState):
    pass


def run_audit():
    logger.info("=========================================")
    logger.info("🚀 QuantumEdge Strategy Logic Audit")
    logger.info("=========================================\n")

    # Base dependencies
    config = {
        "base_order_size_q": 1.0,
        "hedge_trigger_dd": 0.05,
        "grid_step_atr_mult": 2.0,
        "ofi_entry_threshold": 0.5,
    }
    strategy = AdaptiveGridStrategy(config)

    # -----------------------------------------------------
    # Test 1: PASS Mode
    # -----------------------------------------------------
    position = PositionManager()
    market = MockMarketState(
        timestamp=1.0,
        best_bid=100.0,
        best_ask=100.1,
        best_bid_qty=10.0,
        best_ask_qty=10.0,
        last_price=100.0,
        trading_mode=TradingMode.PASS,
    )
    features = FeatureVector(timestamp=1.0, ofi=1.0, vpin=0.1)

    action = strategy.decide(market, features, atr=1.0, position=position)
    orders = (
        []
        if action and action.action_type == "CANCEL_ALL"
        else [action] if action else []
    )
    logger.info(f"Test PASS mode: Expected 0 orders -> Got {len(orders)}")
    logger.info("-" * 40)

    # -----------------------------------------------------
    # Test 2: NEUTRAL Mode (MM)
    # -----------------------------------------------------
    market.trading_mode = TradingMode.NEUTRAL
    features.ofi = 0.8  # Buyer pressure
    action = strategy.decide(market, features, atr=1.0, position=position)
    orders = [action] if action else []
    logger.info(f"Test NEUTRAL mode: Expected 1 order -> Got {len(orders)}")
    logger.info("-" * 40)

    # -----------------------------------------------------
    # Test 3: SCALP Mode
    # -----------------------------------------------------
    market.trading_mode = TradingMode.SCALP
    market.buy_zone_max = 105.0  # We are below this (100.0)
    market.whale_walls = [{"side": "BID", "price": 99.0, "vol": 20.0}]
    action = strategy.decide(market, features, atr=1.0, position=position)
    orders = [action] if action else []

    if action and action.action_type == "BUY" and action.price > 99.0:
        logger.info(
            f"Test SCALP mode: Expected limit order inside bounds -> Got {action.price}"
        )
    else:
        logger.error(f"Test SCALP mode: Failed -> Got {action}")
    logger.info("-" * 40)

    # -----------------------------------------------------
    # Test 4: DCA Mode (Micro-Stop)
    # -----------------------------------------------------
    market.trading_mode = TradingMode.DCA
    position.simulate_fill(100.0, 1.0, "BUY")
    features.ofi = -3.0  # Heavy dumping

    action = strategy.decide(market, features, atr=1.0, position=position)
    orders = (
        []
        if action and action.action_type == "CANCEL_ALL"
        else [action] if action else []
    )
    logger.info(
        f"Test DCA mode: Expected 0 orders (Micro-Stop active) -> Got {len(orders)}"
    )
    logger.info("-" * 40)


if __name__ == "__main__":
    run_audit()
