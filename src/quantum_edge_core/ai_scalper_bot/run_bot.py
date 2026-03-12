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
    DynamicDCAStrategy,
)
from quantum_edge_core.ai_scalper_bot.bot.execution.volatility import OnlineVolatility
from quantum_edge_core.ai_scalper_bot.bot.execution.volatility_oracle import (
    VolatilityOracle,
)
from quantum_edge_core.ai_scalper_bot.bot.execution.position import PositionManager
from quantum_edge_core.ai_scalper_bot.bot.infrastructure.zmq_adapter import ZmqSubStream
from quantum_edge_core.ai_scalper_bot.bot.infrastructure.paper_trader import (
    PaperTrader,
)
from quantum_edge_core.ai_scalper_bot.bot.infrastructure.reporter import (
    SupervisorReporter,
)
from quantum_edge_core.ai_scalper_bot.bot.infrastructure.questdb_telemetry import (
    QuestDbTelemetry,
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
        self.gateway = PaperTrader(self.config)
        self.reporter = SupervisorReporter(
            pub_endpoint=f"tcp://*:{self.config.telemetry_port}",
            service_id=self.config.service_id,
        )
        self.quest_telemetry = QuestDbTelemetry()

        # 1.5 Command Bus (Control Input)
        import zmq

        self.zmq_ctx = zmq.Context()
        self.cmd_sub = self.zmq_ctx.socket(zmq.SUB)
        self.cmd_sub.connect("tcp://127.0.0.1:5558")
        self.cmd_sub.subscribe(f"command.{self.config.service_id}".encode("utf-8"))

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

        trading_mode = getattr(self.config, "trading_mode", "scalper_v1")
        if trading_mode == "spot_grid":
            self.volatility_oracle = VolatilityOracle(self.config.strategy_config)
            self.strategy = DynamicDCAStrategy(self.config.strategy_config)
            logger.info("Initializing SPOT DynamicDCAStrategy")
        else:
            raise NotImplementedError(
                "AdaptiveGridStrategy is disabled. Please set trading_mode='spot_grid' in config."
            )

        self.running = False

    async def run(self):
        logger.info(
            f">>> Bot Engine STARTING Main Loop... Target: {self.config.symbol}"
        )
        self.running = True

        last_heartbeat = 0.0
        last_1m_reset = time.time()

        # ── State retention across iterations ──────────────────
        price = 0.0
        qty = 0.0
        timestamp = 0.0
        is_buyer_maker = False

        try:
            while self.running:
                # ── 0. Process Command Bus (Non-blocking) ──
                try:
                    import zmq
                    import json

                    while True:
                        topic, msg = self.cmd_sub.recv_multipart(zmq.NOBLOCK)
                        cmd = json.loads(msg.decode("utf-8"))
                        market_state = self.cache._current_state
                        if market_state:
                            if "trading_mode" in cmd:
                                from quantum_edge_core.ai_scalper_bot.bot.core.models import (
                                    TradingMode,
                                )

                                try:
                                    mode_str = str(
                                        cmd.get("trading_mode", "PASS")
                                    ).upper()
                                    if mode_str in TradingMode.__members__:
                                        market_state.trading_mode = TradingMode[
                                            mode_str
                                        ]
                                        logger.warning(
                                            f"🤖 SUPERVISOR POLICY: Trading Mode -> {market_state.trading_mode.value}"
                                        )

                                    if (
                                        "buy_zone_max" in cmd
                                        and cmd["buy_zone_max"] is not None
                                    ):
                                        market_state.buy_zone_max = float(
                                            cmd["buy_zone_max"]
                                        )
                                    if (
                                        "sell_zone_min" in cmd
                                        and cmd["sell_zone_min"] is not None
                                    ):
                                        market_state.sell_zone_min = float(
                                            cmd["sell_zone_min"]
                                        )
                                except Exception as p_err:
                                    logger.error(f"Error parsing trade policy: {p_err}")

                            if cmd.get("action") == "PAUSE_ENTRIES":
                                market_state.entries_paused = True
                                logger.warning("🛑 SUPERVISOR COMMAND: Entries Paused!")
                            elif cmd.get("action") == "RESUME_ENTRIES":
                                market_state.entries_paused = False
                                logger.warning(
                                    "🟢 SUPERVISOR COMMAND: Entries Resumed!"
                                )
                            elif cmd.get("action") == "ADJUST_RISK":
                                market_state.risk_multiplier = cmd.get(
                                    "multiplier", 1.0
                                )
                                logger.warning(
                                    f"⚠️ SUPERVISOR COMMAND: Risk Multiplier set to {market_state.risk_multiplier}"
                                )
                except zmq.Again:
                    pass
                except Exception as e:
                    logger.error(f"Error processing command bus: {e}")

                # ── 0.5 Rolling 1M State Reset ──
                now_reset = time.time()
                if now_reset - last_1m_reset >= 60.0:
                    ms = self.cache._current_state
                    if ms:
                        ms.volume_delta_1m = 0.0
                        ms.liquidations_1m = 0
                    last_1m_reset = now_reset

                # 1. Data Ingestion — block up to 100 ms (efficient poll)
                tick = self.market_stream.get_latest_tick(timeout_ms=100)

                # ── No new message: send heartbeat, yield, and retry ──
                if not tick:
                    now = time.time()
                    if now - last_heartbeat >= 1.0:
                        ms = self.cache._current_state
                        await self.reporter.send_heartbeat(
                            self.strategy.state,
                            self.position.state.unrealized_pnl,
                            self.position.total_qty,
                            market_state=ms,
                        )
                        # ILP Portfolio logging
                        eq = getattr(ms, "equity_now", 0.0) if ms else 0.0
                        self.quest_telemetry.log_portfolio_state(
                            symbol=self.config.symbol,
                            equity=eq,
                            unrealized_pnl=self.position.state.unrealized_pnl,
                            position_qty=self.position.total_qty,
                        )
                        last_heartbeat = now
                    await asyncio.sleep(0.01)
                    continue

                # ── Event type filter: process kline, trade AND depth ──
                # Hub publishes whale, metrics, heartbeat events
                # on the same ZMQ bus — skip those.
                ev_type = tick.get("type", "") or tick.get("event_type", "")

                # ── Handle Liquidation Immediately ──
                if ev_type == "liquidation":
                    l_side = tick.get("side", "N/A")
                    l_price = float(tick.get("price", 0.0))
                    l_qty = float(tick.get("qty", 0.0))
                    ms = self.cache._current_state
                    if ms:
                        ms.liquidations_1m += 1
                    logger.warning(
                        f"LIQUIDATION DETECTED: {l_side} {l_qty} BTC @ {l_price}"
                    )
                    await asyncio.sleep(0.001)
                    continue

                if ev_type not in (
                    "KlineEvent",
                    "kline",
                    "TradeEvent",
                    "trade",
                    "MarketTrade",
                    "depth",
                    "OrderBookUpdate",  # ← L2 depth events
                    "",  # allow untyped raw payloads
                ):
                    await asyncio.sleep(0.001)
                    continue

                # ── Detect depth (orderbook) event ───────────────
                is_depth = ev_type in ("depth", "OrderBookUpdate") or (
                    "bids" in tick and "asks" in tick
                )

                # ── Extract BBO & Walls from depth payload ───────────────
                best_bid = 0.0
                best_ask = 0.0
                best_bid_qty = 0.0
                best_ask_qty = 0.0
                whale_walls = []

                if is_depth:
                    bids = tick.get("bids", [])
                    asks = tick.get("asks", [])
                    whale_walls = tick.get("whale_walls", [])

                    if bids and len(bids[0]) >= 2:
                        best_bid = float(bids[0][0])
                        best_bid_qty = float(bids[0][1])
                    if asks and len(asks[0]) >= 2:
                        best_ask = float(asks[0][0])
                        best_ask_qty = float(asks[0][1])

                    # Depth events don't carry trade price — use mid or last known
                    if best_bid and best_ask and price <= 0.0:
                        price = (best_bid + best_ask) / 2.0

                # ── Normalize tick data ──────────────────────────
                if not is_depth:
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

                # Create normalized dict for cache (with BBO & Walls from depth)
                norm_tick = {
                    "p": price,
                    "q": qty,
                    "T": timestamp * 1000,
                    "m": is_buyer_maker,
                    "b": best_bid,  # Best bid price
                    "a": best_ask,  # Best ask price
                    "B": best_bid_qty,  # Best bid qty
                    "A": best_ask_qty,  # Best ask qty
                    "W": whale_walls,  # List of WhaleWall dicts
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

                    # Update Maco-metrics (Volume Delta)
                    if (
                        not is_depth
                        and ev_type in ("trade", "TradeEvent", "MarketTrade", "")
                        and qty > 0.0
                    ):
                        if is_buyer_maker:
                            market_state.volume_delta_1m -= qty
                        else:
                            market_state.volume_delta_1m += qty

                    # Update Alpha Features
                    feat_vec = self.features.update(tick_obj, market_state)
                    atr_val = self.volatility.update(market_state.last_price)
                    market_state.atr = atr_val

                    if getattr(self, "volatility_oracle", None):
                        # Use kline close to update oracle, or fallback to last_price
                        kline = tick.get("k")
                        if (
                            kline and isinstance(kline, dict) and kline.get("x")
                        ):  # x=is_closed
                            self.volatility_oracle.add_close_price(
                                float(kline.get("c", market_state.last_price))
                            )

                        market_state.vol_index = (
                            self.volatility_oracle.calculate_volatility_index()
                        )
                        market_state.grid_spacing_pct = (
                            self.volatility_oracle.get_dynamic_grid_spacing(
                                market_state.vol_index
                            )
                        )

                    # 3. Decision
                    # Format log with walls info
                    walls = market_state.whale_walls or []
                    walls_info = ""
                    if walls:
                        # Extract first wall's price info safely (handles both object and dict formats)
                        first_wall = walls[0]
                        w_side = (
                            getattr(first_wall, "side", first_wall.get("side", ""))
                            if isinstance(first_wall, dict)
                            else getattr(
                                first_wall, "side", getattr(first_wall, "side", "")
                            )
                        )
                        if isinstance(first_wall, dict):
                            w_side, w_price = first_wall.get(
                                "side", "?"
                            ), first_wall.get("price", 0.0)
                        else:
                            w_side, w_price = getattr(first_wall, "side", "?"), getattr(
                                first_wall, "price", 0.0
                            )
                        walls_info = f" | Walls: {len(walls)} ({w_side}: {w_price})"

                    logger.debug(
                        f"TICK: Price={market_state.last_price:.2f}, OFI={feat_vec.ofi:.4f}, ATR={atr_val:.4f}, B={market_state.best_bid_qty}, A={market_state.best_ask_qty}{walls_info}"
                    )

                    action = self.strategy.decide(
                        market_state, feat_vec, atr_val, self.position
                    )

                    # 4. Execution (PaperTrader — Shadow Mode)
                    active_signal_name = "HOLD"
                    if action:
                        active_signal_name = action.action_type
                        logger.warning(
                            f"🚀 SIGNAL GENERATED: {action.action_type} @ {action.price} | Reason: {action.reason}"
                        )
                        self.position.simulate_fill(
                            action.price,
                            action.qty,
                            action.action_type,
                            self.config.symbol,
                        )
                        await self.gateway.execute(action)

                    # 4.5 Telemetry publishing
                    dist_pct = 0.0
                    if market_state.whale_walls:
                        closest_walls = sorted(
                            market_state.whale_walls,
                            key=lambda w: abs(
                                (
                                    w.get("price", 0.0)
                                    if isinstance(w, dict)
                                    else getattr(w, "price", 0.0)
                                )
                                - market_state.last_price
                            ),
                        )
                        n_price = (
                            closest_walls[0].get("price", 0.0)
                            if isinstance(closest_walls[0], dict)
                            else getattr(closest_walls[0], "price", 0.0)
                        )
                        if market_state.last_price > 0:
                            dist_pct = (
                                abs(n_price - market_state.last_price)
                                / market_state.last_price
                            )

                    await self.reporter.send_telemetry(
                        market_state=market_state,
                        ofi=feat_vec.ofi,
                        action=active_signal_name,
                        closest_wall_dist_pct=dist_pct,
                    )

                # 5. Reporting (Throttled)
                now = time.time()
                if now - last_heartbeat >= 1.0:
                    await self.reporter.send_heartbeat(
                        self.strategy.state,
                        self.position.state.unrealized_pnl,
                        self.position.total_qty,
                    )

                    ms = self.cache._current_state
                    eq = getattr(ms, "equity_now", 0.0) if ms else 0.0
                    self.quest_telemetry.log_portfolio_state(
                        symbol=self.config.symbol,
                        equity=eq,
                        unrealized_pnl=self.position.state.unrealized_pnl,
                        position_qty=self.position.total_qty,
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
