"""
QuantumEdge AI Scalper Bot - Main Entry Point.
Assembles Data, Logic, and Execution layers into an AsyncIO High-Frequency Loop.
Target Exchange: BingX (Integration Phase)
"""

import asyncio
import logging
import time

from quantum_edge_core.ai_scalper_bot.bot.core.config import Config
from quantum_edge_core.ai_scalper_bot.bot.core.orderbook import OrderBookCache
from quantum_edge_core.ai_scalper_bot.bot.features.facade import FeatureEngine
from quantum_edge_core.ai_scalper_bot.bot.execution.strategy_core import (
    AdaptiveGridStrategy,
)
from quantum_edge_core.ai_scalper_bot.bot.execution.volatility import OnlineVolatility
from quantum_edge_core.ai_scalper_bot.bot.execution.position import PositionManager
from quantum_edge_core.ai_scalper_bot.bot.infrastructure.zmq_adapter import ZmqSubStream
from quantum_edge_core.ai_scalper_bot.bot.infrastructure.exchange import (
    BingXExecutionGateway,
)
from src.quantum_edge_core.ai_scalper_bot.bot.infrastructure.reporter import (
    TelemetryPublisher,
)

# Configure Logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("QuantumEdgeBot")


class BotEngine:
    def __init__(self):
        logger.info("Initializing QuantumEdge AI Scalper (BingX Edition)...")
        self.config = Config()

        # 1. Infrastructure (I/O)
        self.market_stream = ZmqSubStream(
            endpoint=f"tcp://127.0.0.1:{self.config.market_data_port}",
            topic="",  # Mock sends JSON without topic prefix
        )
        self.gateway = BingXExecutionGateway(self.config)
        self.reporter = TelemetryPublisher(
            pub_endpoint=f"tcp://*:{self.config.supervisor_port}"
        )

        # 2. Memory (State)
        self.cache = OrderBookCache()
        self.position = PositionManager()

        # 3. Logic (Features & Strategy)
        self.features = FeatureEngine(
            vpin_bucket_vol=10.0, vpin_window=50  # Could also move to config
        )
        self.volatility = OnlineVolatility(
            alpha=self.config.strategy_config.get("atr_alpha", 0.01)
        )
        self.strategy = AdaptiveGridStrategy(self.config.strategy_config)

        self.running = False

    async def run(self):
        logger.info(
            f">>> Bot Engine STARTING Main Loop... Target: {self.config.symbol}"
        )
        self.running = True

        # Start Telemetry as a background task
        await self.reporter.start()

        last_heartbeat = 0.0

        try:
            while self.running:
                # 1. Data Ingestion (Non-blocking check)
                tick = self.market_stream.get_latest_tick(timeout_ms=1)

                if tick:
                    current_ts = time.time()

                    # 2. Critical Path: Update State
                    self.cache.update(tick)

                    # Features need the Struct
                    market_state = self.cache._current_state

                    if market_state:  # Ensure we have initial state
                        # Re-parse for Feature usage
                        from quantum_edge_core.ai_scalper_bot.bot.core.models import (
                            MarketTick,
                        )

                        tick_obj = MarketTick(
                            price=float(tick["p"]),
                            quantity=float(tick["q"]),
                            timestamp=float(tick.get("T", 0)),
                            is_buyer_maker=bool(tick.get("m", False)),
                        )

                        # Update Alpha Features
                        feat_vec = self.features.update(tick_obj, market_state)
                        atr_val = self.volatility.update(market_state.last_price)

                        # 3. Decision
                        # DEBUG LOGGING for UAT
                        debug_msg = (
                            f"DEBUG: Price={market_state.last_price:.2f}, "
                            f"OFI={feat_vec.ofi:.4f}, ATR={atr_val:.4f}, "
                            f"B={market_state.best_bid_qty}, "
                            f"A={market_state.best_ask_qty}"
                        )
                        logger.info(debug_msg)

                        action = self.strategy.decide(
                            market_state, feat_vec, atr_val, self.position
                        )

                        # 4. Execution
                        if action:
                            logger.info(f"!!! SIGNAL: {action}")
                            # Execute on BingX
                            # Position Update: Optimistic update (or wait for fill?)
                            # Strategy assumes fill.
                            self.position.simulate_fill(
                                action.price, action.qty, action.action_type
                            )

                            # Fire and forget (or await?)
                            # User template awaits: "await self.gateway.execute(action)"
                            # We can await. It might block loop for HTTP RTT (~100ms).
                            # For Scalper, usually create_task. But for UAT stability, await is safer to see result.
                            # We'll use create_task to maintain speed, unless user template demanded blocking.
                            # User template: "await self.gateway.execute(action)".
                            # I will Use await to ensure we see the result log "✅ BINGX...".
                            await self.gateway.execute(action)

                # 5. Reporting (Throttled)
                now = time.time()
                if now - last_heartbeat >= 1.0:
                    await self.reporter.send_heartbeat(
                        self.strategy.state,
                        self.position.state.unrealized_pnl,
                        self.position.total_qty,
                    )
                    last_heartbeat = now

                # Yield control to event loop
                await asyncio.sleep(0.0001)

        except asyncio.CancelledError:
            logger.info("Bot Engine Stopping...")
        except Exception as e:
            logger.exception(f"CRITICAL MAIN LOOP ERROR: {e}")
        finally:
            await self.shutdown()

    async def shutdown(self):
        logger.info("Shutting down infrastructure...")
        await self.gateway.close()
        await self.reporter.stop()
        self.market_stream.close()


if __name__ == "__main__":
    bot = BotEngine()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        pass
