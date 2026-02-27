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
    BinanceExecutionGateway,
)
from quantum_edge_core.ai_scalper_bot.bot.infrastructure.reporter import (
    SupervisorReporter,
)

# Configure Logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("QuantumEdgeBot")


class BotEngine:
    def __init__(self):
        logger.info("Initializing QuantumEdge AI Scalper (Binance Edition)...")
        self.config = Config()

        # 1. Infrastructure (I/O)
        self.market_stream = ZmqSubStream(
            endpoint=f"tcp://127.0.0.1:{self.config.market_data_port}",
            topic="",  # Global subscription — receive ALL topics from Hub
        )
        self.gateway = BinanceExecutionGateway(self.config)
        self.reporter = SupervisorReporter(
            pub_endpoint=f"tcp://*:{self.config.telemetry_port}",
            service_id=self.config.service_id,
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

        last_heartbeat = 0.0

        # ── State retention across iterations ──────────────────
        price = 0.0
        qty = 0.0
        timestamp = 0.0
        is_buyer_maker = False

        try:
            while self.running:
                # 1. Data Ingestion — block up to 100 ms (efficient poll)
                tick = self.market_stream.get_latest_tick(timeout_ms=100)

                # ── No new message: send heartbeat, yield, and retry ──
                if not tick:
                    now = time.time()
                    if now - last_heartbeat >= 1.0:
                        await self.reporter.send_heartbeat(
                            self.strategy.state,
                            self.position.state.unrealized_pnl,
                            self.position.total_qty,
                        )
                        last_heartbeat = now
                    await asyncio.sleep(0.01)
                    continue

                # ── Event type filter: only process kline / trade ──
                # Hub also publishes whale, metrics, heartbeat events
                # on the same ZMQ bus — skip those.
                ev_type = tick.get("type", "") or tick.get("event_type", "")
                if ev_type not in (
                    "KlineEvent", "kline",
                    "TradeEvent", "trade",
                    "MarketTrade",
                    "",  # allow untyped raw payloads
                ):
                    await asyncio.sleep(0.001)
                    continue

                # ── Normalize tick data ──────────────────────────
                # Priority: kline k.c → price → p → close → 0.0
                kline = tick.get("k")  # Binance raw kline wrapper
                if kline and isinstance(kline, dict):
                    price = float(kline.get("c", 0.0))
                    qty = float(kline.get("v", 0.0))
                else:
                    price = float(
                        tick.get("price")
                        or tick.get("p")
                        or tick.get("close")
                        or 0.0
                    )
                    qty = float(
                        tick.get("quantity")
                        or tick.get("q")
                        or tick.get("size")
                        or tick.get("volume")
                        or 0.0
                    )

                # ── Guard: skip zero-price events ──
                if price <= 0.0:
                    await asyncio.sleep(0.01)
                    continue

                # T/timestamp handling (ns or ms or s)
                ts_raw = (
                    tick.get("T") or tick.get("ts_ns") or tick.get("timestamp") or 0
                )
                timestamp = float(ts_raw)
                if timestamp > 1e18:
                    timestamp /= 1e9  # ns to s
                elif timestamp > 1e12:
                    timestamp /= 1e3  # ms to s

                # m/is_buyer_maker handling
                is_buyer_maker = bool(tick.get("m", False))
                if "taker_side" in tick:
                    is_buyer_maker = tick["taker_side"] == "sell"
                elif "side" in tick:
                    is_buyer_maker = tick["side"] == "sell"

                # Create normalized dict for cache
                norm_tick = {
                    "p": price,
                    "q": qty,
                    "T": timestamp * 1000,
                    "m": is_buyer_maker,
                }

                # 2. Critical Path: Update State
                self.cache.update(norm_tick)
                market_state = self.cache._current_state

                if market_state:
                    from quantum_edge_core.ai_scalper_bot.bot.core.models import (
                        MarketTick,
                    )

                    tick_obj = MarketTick(
                        price=price,
                        quantity=qty,
                        timestamp=timestamp,
                        is_buyer_maker=is_buyer_maker,
                    )

                    # Update Alpha Features
                    feat_vec = self.features.update(tick_obj, market_state)
                    atr_val = self.volatility.update(market_state.last_price)

                    # 3. Decision
                    logger.info(
                        f"TICK: Price={market_state.last_price:.2f}, OFI={feat_vec.ofi:.4f}, ATR={atr_val:.4f}, B={market_state.best_bid_qty}, A={market_state.best_ask_qty}"
                    )

                    action = self.strategy.decide(
                        market_state, feat_vec, atr_val, self.position
                    )

                    # 4. Execution
                    if action:
                        logger.info(f"!!! SIGNAL: {action}")
                        self.position.simulate_fill(
                            action.price, action.qty, action.action_type
                        )
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
                await asyncio.sleep(0.01)

        except asyncio.CancelledError:
            logger.info("Bot Engine Stopping...")
        except Exception as e:
            logger.exception(f"CRITICAL MAIN LOOP ERROR: {e}")
        finally:
            await self.shutdown()

    async def shutdown(self):
        logger.info("Shutting down infrastructure...")
        await self.gateway.close()
        self.reporter.close()
        self.market_stream.close()


if __name__ == "__main__":
    bot = BotEngine()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        pass
