import asyncio
import logging
import sys

from quantum_edge_core.ai_scalper_bot.bot.core.models import MarketState, TradingMode, MarketTick
from quantum_edge_core.ai_scalper_bot.bot.features.facade import FeatureVector
from quantum_edge_core.ai_scalper_bot.bot.execution.position import PositionManager
from quantum_edge_core.ai_scalper_bot.bot.execution.strategy_core import AdaptiveGridStrategy

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
        "ofi_entry_threshold": 0.5
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
        trading_mode=TradingMode.PASS
    )
    features = FeatureVector(timestamp=1.0, ofi=1.0, vpin=0.1)
    
    action = strategy.decide(market, features, atr=1.0, position=position)
    logger.info("Test 1: PASS Mode (Empty Position)")
    if action and action.action_type == "CANCEL_ALL":
        logger.info("✅ Assertion OK: 1 action generated -> CANCEL_ALL")
    else:
        logger.error(f"❌ Failed: Expected CANCEL_ALL, got {action}")
    logger.info("-" * 40)

    # -----------------------------------------------------
    # Test 2: NEUTRAL Mode (MM)
    # -----------------------------------------------------
    market.trading_mode = TradingMode.NEUTRAL
    features.ofi = 0.8 # Buyer pressure
    action = strategy.decide(market, features, atr=1.0, position=position)
    logger.info("Test 2: NEUTRAL Mode (High OFI)")
    if action and action.action_type == "BUY":
        logger.info("✅ Assertion OK: 1 action generated -> BUY (Neutral MM)")
    else:
        logger.error(f"❌ Failed: Expected BUY, got {action}")
    logger.info("-" * 40)

    # -----------------------------------------------------
    # Test 3: SCALP Mode
    # -----------------------------------------------------
    market.trading_mode = TradingMode.SCALP
    market.buy_zone_max = 105.0 # We are below this (100.0)
    market.whale_walls = [{"side": "BID", "price": 99.0, "vol": 20.0}]
    action = strategy.decide(market, features, atr=1.0, position=position)
    logger.info("Test 3: SCALP Mode (Below buy_zone_max)")
    
    # Needs to frontrun the L2 wall at 99.0 (ticks above = 2, ticksize = 0.1 -> 99.2)
    if action and action.action_type == "BUY" and action.price > 99.0:
        logger.info(f"✅ Assertion OK: Limit action generated based on L2 wall -> BUY @ {action.price}")
    else:
        logger.error(f"❌ Failed: Expected Limit BUY frontrunning L2 wall, got {action}")
    logger.info("-" * 40)

    # -----------------------------------------------------
    # Test 4: DCA Mode (Micro-Stop)
    # -----------------------------------------------------
    market.trading_mode = TradingMode.DCA
    position.simulate_fill(100.0, 1.0, "BUY")
    features.ofi = -3.0 # Heavy dumping
    
    action = strategy.decide(market, features, atr=1.0, position=position)
    logger.info("Test 4: DCA Mode (Micro-Stop active due to negative OFI)")
    
    if action and action.action_type == "CANCEL_ALL":
        logger.info("✅ Assertion OK: Micro-stop cancelled pending grid orders.")
    else:
        logger.error(f"❌ Failed: Expected CANCEL_ALL Micro-Stop, got {action}")
    logger.info("-" * 40)

if __name__ == "__main__":
    run_audit()
