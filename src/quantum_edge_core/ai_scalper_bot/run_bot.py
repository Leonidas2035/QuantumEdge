"""
QuantumEdge AI Scalper Bot - Main Entry Point.
Assembles Data, Logic, and Execution layers into an AsyncIO High-Frequency Loop.
Target Exchange: BingX (Integration Phase)
"""

import asyncio
import collections
import logging
import logging.config
import time
import yaml
from decimal import Decimal
from pathlib import Path

from quantum_edge_core.ai_scalper_bot.bot.core.config import Config
from quantum_edge_core.ai_scalper_bot.bot.core.orderbook import OrderBookCache
from quantum_edge_core.ai_scalper_bot.bot.features.facade import FeatureEngine
from quantum_edge_core.ai_scalper_bot.bot.execution.strategy_core import (
    BotState,
    AdaptiveGridStrategy,
)
from quantum_edge_core.ai_scalper_bot.bot.execution.volatility import OnlineVolatility
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
logging.config.dictConfig(
    yaml.safe_load(Path("config/logging.yaml").read_text(encoding="utf-8"))
)
logger = logging.getLogger("QuantumEdgeBot")


def cleanup_old_logs(days: int = 14):
    for log_file in Path(".").glob("*.log.*"):
        if log_file.stat().st_mtime < (time.time() - days * 86400):
            try:
                log_file.unlink()
            except OSError as e:
                logger.warning(f"Failed to delete old log {log_file}: {e}")


class BotEngine:
    """Main bot engine with Supervisor-respecting command handling.

    The bot honours ``PAUSE_ENTRIES`` / ``RESUME_ENTRIES`` directives
    from the Supervisor.  The previous "Iron Lock" pattern that forcibly
    reset those flags on every tick has been removed.
    """

    def __init__(self):
        logger.info("Initializing QuantumEdge AI Scalper (Binance Edition)...")
        self.config = Config()

        # 1. Infrastructure (I/O)
        self.market_stream = ZmqSubStream(
            endpoint=f"tcp://127.0.0.1:{self.config.market_data_port}",
            topic="",  # Global subscription — receive ALL topics from Hub
            connect_now=False,
        )
        import os

        execution_mode = getattr(self.config, "execution_mode", "paper").lower()
        use_testnet = getattr(self.config, "use_testnet", False)

        if execution_mode in ("bingx", "live"):
            logger.warning(
                "🚀 BINGX EXECUTION MODE"
                + (" (VST Demo)" if use_testnet else " (MAINNET - REAL MONEY)")
            )
            from quantum_edge_core.ai_scalper_bot.bot.infrastructure.bingx_gateway import (
                BingXExecutionGateway,
            )

            self.gateway = BingXExecutionGateway(self.config)
        else:
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
        cmd_endpoint = f"tcp://127.0.0.1:{self.config.policy_port}"
        self.cmd_sub.connect(cmd_endpoint)
        self.cmd_sub.subscribe(f"command.{self.config.service_id}".encode("utf-8"))
        logger.info(f"🔌 Command bus connected to {cmd_endpoint}, subscribed to command.{self.config.service_id}")

        # 2. Memory (State)
        self.cache = OrderBookCache()
        self.ofi_history = collections.deque(maxlen=1000)
        self.price_history = collections.deque(maxlen=1000)
        self.position = PositionManager(
            mode=getattr(self.config, "trading_mode", "scalper_v1"),
            initial_quote_balance=float(self.gateway.quote_balance),
        )
        logger.info(
            "PositionManager initialised with quote_balance=%.2f",
            float(self.position.state.quote_balance),
        )

        from quantum_edge_core.ai_scalper_bot.bot.execution.position import PortfolioSynchronizer
        self.portfolio_sync = None
        if execution_mode in ("bingx", "live"):
            api_key = self.config.bingx_testnet_api_key if use_testnet else self.config.bingx_api_key
            api_secret = self.config.bingx_testnet_secret if use_testnet else self.config.bingx_secret
            if api_key and api_secret:
                self.portfolio_sync = PortfolioSynchronizer(api_key, api_secret, self.position)
                logger.info("PortfolioSynchronizer armed for background sync.")

        # 3. Logic (Features & Strategy)
        self.features = FeatureEngine(
            vpin_bucket_vol=10.0, vpin_window=50  # Could also move to config
        )
        self.volatility = OnlineVolatility(
            alpha=self.config.strategy_config.get("atr_alpha", 0.01)
        )

        self.strategy = AdaptiveGridStrategy(self.config.strategy_config)
        logger.info("Initializing AdaptiveGridStrategy for AI Scalper")

        self.running = False

        # Telemetry Cache
        self.last_telemetry_fetch = 0.0
        self.cached_leverage = 20.0
        self.cached_liq_price = 0.0
        self.cached_unrealized_pnl = 0.0
        self.cached_position_qty = 0.0
        self.last_eval_log = 0.0

        # Truncate QuestDB realized_trades table to clear old test data
        try:
            import requests
            host = os.getenv("MARKET_DATA_QUEST_HOST", "127.0.0.1")
            port = os.getenv("MARKET_DATA_QUEST_REST_PORT", "9000")
            url = f"http://{host}:{port}/exec"
            resp = requests.get(url, params={"query": "TRUNCATE TABLE realized_trades;"}, timeout=2.0)
            if resp.status_code == 200:
                logger.info("QuestDB realized_trades table truncated successfully.")
            else:
                logger.warning(f"Failed to truncate realized_trades table: {resp.text}")
        except Exception as e:
            logger.warning(f"Could not truncate realized_trades table: {e}")

    async def telemetry_loop(self):
        """Independent task for continuous 2-second telemetry emission."""
        logger.info("📡 Starting independent telemetry loop (2s heartbeat)")
        while self.running:
            try:
                await asyncio.sleep(2.0)
                await self._update_portfolio_telemetry()
                
                eq = float(self.position.state.quote_balance)
                unrealized_pnl = self.cached_unrealized_pnl or float(self.position.state.unrealized_pnl)
                pos_qty = self.cached_position_qty or float(self.position.total_qty)
                ms = self.cache._current_state
                
                await self.reporter.send_heartbeat(
                    self.strategy.state,
                    unrealized_pnl,
                    pos_qty,
                    market_state=ms,
                    equity=eq,
                    trading_mode=getattr(self.config, "trading_mode", "spot_grid"),
                )
                
                self.quest_telemetry.log_portfolio_state(
                    symbol=self.config.symbol,
                    equity=eq,
                    unrealized_pnl=unrealized_pnl,
                    position_qty=pos_qty,
                    leverage=self.cached_leverage,
                    liquidation_price=self.cached_liq_price,
                )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in telemetry loop: {e}")

    async def _update_portfolio_telemetry(self):
        now = time.time()
        if now - self.last_telemetry_fetch >= 5.0:
            try:
                # 1. Fetch positions
                if hasattr(self.gateway, "fetch_positions_async"):
                    positions = await self.gateway.fetch_positions_async()
                    if positions:
                        total_qty = 0.0
                        total_pnl = 0.0
                        first_pos = positions[0]
                        self.cached_leverage = first_pos.get("leverage", 20.0)
                        self.cached_liq_price = first_pos.get("liquidation_price", 0.0)
                        
                        long_qty = Decimal("0.0")
                        short_qty = Decimal("0.0")
                        long_avg = Decimal("0.0")
                        short_avg = Decimal("0.0")
                        long_pnl = Decimal("0.0")
                        short_pnl = Decimal("0.0")
                        
                        for pos in positions:
                            qty_val = pos.get("size", 0.0)
                            avg_entry = pos.get("entry_price", 0.0) or pos.get("avg_price", 0.0) or pos.get("price", 0.0)
                            pnl_val = pos.get("unrealized_pnl", 0.0)
                            
                            qty_dec = Decimal(str(qty_val))
                            avg_dec = Decimal(str(avg_entry))
                            pnl_dec = Decimal(str(pnl_val))
                            
                            if pos.get("side") == "SHORT":
                                short_qty = qty_dec
                                short_avg = avg_dec
                                short_pnl = pnl_dec
                                total_qty -= qty_val
                            else:
                                long_qty = qty_dec
                                long_avg = avg_dec
                                long_pnl = pnl_dec
                                total_qty += qty_val
                            total_pnl += pos.get("unrealized_pnl", 0.0)
                            
                        self.cached_position_qty = total_qty
                        self.cached_unrealized_pnl = total_pnl
                        
                        # Sync PositionManager
                        self.position.long_state.total_qty = long_qty
                        self.position.long_state.avg_price = long_avg
                        self.position.long_state.unrealized_pnl = long_pnl
                        
                        self.position.short_state.total_qty = short_qty
                        self.position.short_state.avg_price = short_avg
                        self.position.short_state.unrealized_pnl = short_pnl
                    else:
                        self.cached_position_qty = 0.0
                        self.cached_unrealized_pnl = 0.0
                        self.cached_liq_price = 0.0
                        
                        self.position.long_state.total_qty = Decimal("0.0")
                        self.position.long_state.avg_price = Decimal("0.0")
                        self.position.long_state.unrealized_pnl = Decimal("0.0")
                        
                        self.position.short_state.total_qty = Decimal("0.0")
                        self.position.short_state.avg_price = Decimal("0.0")
                        self.position.short_state.unrealized_pnl = Decimal("0.0")

                # 2. Fetch balance
                if hasattr(self.gateway, "fetch_balance_async"):
                    actual_balance = await self.gateway.fetch_balance_async()
                    self.position.state.quote_balance = Decimal(str(actual_balance))
            except Exception as e:
                logger.error(f"Error in telemetry fetch: {e}")
            self.last_telemetry_fetch = now

    # ── Supervisor command handler ────────────────────────────────
    def handle_supervisor_command(self, cmd: dict) -> None:
        """Process a single command dict from the Supervisor command bus.

        Supported actions:
        - ``PAUSE_ENTRIES``: halt all new entries until ``RESUME_ENTRIES``.
        - ``RESUME_ENTRIES``: re-enable entries after a pause.
        - ``ADJUST_RISK``: set ``market_state.risk_multiplier``.
        - ``trading_mode`` field: switch the active ``TradingMode``.
        """
        market_state = self.cache._current_state

        action: str = cmd.get("action", "")

        if action == "PAUSE_ENTRIES":
            self.gateway.entries_paused = True
            self.strategy.state = BotState.PAUSED
            if market_state:
                market_state.entries_paused = True
            logger.warning("🛑 Bot State set to PAUSED by Supervisor.")
            return

        if action == "RESUME_ENTRIES":
            self.gateway.entries_paused = False
            self.strategy.state = BotState.RUNNING
            if market_state:
                market_state.entries_paused = False
            logger.warning("🟢 Bot State set to RUNNING by Supervisor.")
            return

        if action == "ADJUST_RISK":
            multiplier = float(cmd.get("multiplier", 1.0))
            if market_state:
                market_state.risk_multiplier = multiplier
            logger.warning(
                "⚠️ SUPERVISOR COMMAND: Risk Multiplier set to %s",
                multiplier,
            )
            return

        # ── Trading mode / zone updates (no action field) ─────────
        if market_state and "trading_mode" in cmd:
            from quantum_edge_core.ai_scalper_bot.bot.core.models import (
                TradingMode,
            )

            try:
                mode_str = str(cmd.get("trading_mode", "PASS")).upper()
                if mode_str in TradingMode.__members__:
                    market_state.trading_mode = TradingMode[mode_str]
                    logger.warning(
                        "🤖 SUPERVISOR POLICY: Trading Mode -> %s",
                        market_state.trading_mode.value,
                    )

                if "buy_zone_max" in cmd and cmd["buy_zone_max"] is not None:
                    market_state.buy_zone_max = float(cmd["buy_zone_max"])
                if "sell_zone_min" in cmd and cmd["sell_zone_min"] is not None:
                    market_state.sell_zone_min = float(cmd["sell_zone_min"])
            except Exception as parse_err:
                logger.error("Error parsing trade policy: %s", parse_err)

    async def warm_up(self):
        symbol = self.config.symbol
        logger.info(f"[WARM-UP] Fetching historical microstructure data for {symbol} from QuestDB...")
        try:
            from quantum_edge_core.market_data.tsdb.query_builder import QuestDBQueryBuilder
            db = QuestDBQueryBuilder()
            history = await db.get_microstructure(symbol, minutes=15)
            logger.info(f"[WARM-UP] Received {len(history)} records from QuestDB. Appending to deques...")
            for record in history:
                self.ofi_history.append(record.get('ofi_raw', 0.0))
                self.price_history.append(record.get('mid_price', 0.0))
            logger.info(f"[WARM-UP] Warm-up complete. Loaded {len(self.ofi_history)} OFI values, {len(self.price_history)} price values.")
        except Exception as e:
            logger.error(f"[WARM-UP] Error during warm-up: {e}")

    async def run(self) -> None:
        logger.info(
            f">>> Bot Engine STARTING Main Loop... Target: {self.config.symbol}"
        )
        await self.warm_up()
        self.market_stream.connect()
        self.running = True

        # ── AUTO-START: Force RUNNING state on boot ───────────
        self.strategy.state = BotState.RUNNING
        self.gateway.status = "RUNNING"
        self.gateway.entries_paused = False
        logger.info(
            "Auto-start: strategy.state=%s, gateway.status=%s, entries_paused=%s",
            self.strategy.state.name,
            self.gateway.status,
            self.gateway.entries_paused,
        )

        # ── Fetch actual balance from exchange (async, now that loop is running) ───────────
        if hasattr(self.gateway, "fetch_balance_async"):
            actual_balance = await self.gateway.fetch_balance_async()
            # Update PositionManager with real balance

            self.position.state.quote_balance = Decimal(str(actual_balance))
            logger.info(
                f"💰 PositionManager updated with real balance: {actual_balance:.2f}"
            )

        # ── Broadcast initial state to Dashboard ──────────────
        await self.reporter.send_initial_state(
            equity=float(self.position.state.quote_balance),
            trading_mode=getattr(self.config, "trading_mode", "spot_grid"),
        )
        # Also log initial portfolio to QuestDB
        self.quest_telemetry.log_portfolio_state(
            symbol=self.config.symbol,
            equity=float(self.position.state.quote_balance),
            unrealized_pnl=0.0,
            position_qty=0.0,
        )

        # ── Start Telemetry Task ──────────────
        telemetry_task = asyncio.create_task(self.telemetry_loop())

        # ── Start Background CCXT Sync ──────────
        if getattr(self, "portfolio_sync", None):
            asyncio.create_task(self.portfolio_sync.sync_loop())

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
                        self.handle_supervisor_command(cmd)
                except zmq.Again:
                    pass
                except Exception as e:
                    logger.error("Error processing command bus: %s", e)

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

                # ── No new message: yield, and retry ──
                if not tick:
                    await asyncio.sleep(0.01)
                    continue

                # ── Event type filter: process kline, trade AND depth ──
                # Hub publishes whale, metrics, heartbeat events
                # on the same ZMQ bus — skip those.
                ev_type = tick.get("type", "") or tick.get("event_type", "")

                # ── Handle Account Delta (Order Fills) Immediately ──
                if ev_type == "hub.account_delta.v1":
                    from quantum_edge_core.ai_scalper_bot.bot.execution.strategy_core import TradeAction
                    data = tick.get("data", {})
                    patch = data.get("patch", {})
                    orders_update = []
                    spot = patch.get("spot")
                    if spot and isinstance(spot, dict):
                        orders_update.extend(spot.get("orders_update") or [])
                    usdm = patch.get("usdm")
                    if usdm and isinstance(usdm, dict):
                        orders_update.extend(usdm.get("orders_update") or [])

                    for order in orders_update:
                        if order.get("status") == "FILLED":
                            side = order.get("side", "").upper()
                            pos_side = order.get("positionSide")

                            is_entry = False
                            if pos_side and pos_side.upper() == "SHORT":
                                is_entry = (side == "SELL")
                            else:
                                is_entry = (side == "BUY")

                            price = Decimal(str(order.get("price", "0")))
                            qty = Decimal(str(order.get("origQty", "0")))

                            if is_entry:
                                action = TradeAction(
                                    action_type="ORDER_FILLED",
                                    price=price,
                                    qty=qty,
                                    reason=f"side={side}|spacing_pct=0.012",
                                )
                                logger.warning(
                                    f"🔔 ZMQ DETECTED ENTRY ORDER FILLED: {side} ({pos_side}) {qty} @ {price} | Executing counter-order..."
                                )
                                self.position.simulate_fill(
                                    price,
                                    qty,
                                    side,
                                    self.config.symbol,
                                    position_side=pos_side,
                                )
                                await self.gateway.execute(action)
                            else:
                                logger.warning(
                                    f"🔔 ZMQ DETECTED EXIT ORDER FILLED: {side} ({pos_side}) {qty} @ {price}."
                                )
                                self.position.simulate_fill(
                                    price,
                                    qty,
                                    side,
                                    self.config.symbol,
                                    position_side=pos_side,
                                )
                    await asyncio.sleep(0.001)
                    continue

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
                    hub_mid = tick.get("mid_price")
                    if hub_mid and float(hub_mid) > 0:
                        price = float(hub_mid)
                    elif best_bid and best_ask and price <= 0.0:
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

                # STALE_DATA guard: reject data older than 5 seconds
                if timestamp > 0 and (time.time() - timestamp) > 5.0:
                    logger.warning(
                        f"STALE_DATA: Rejecting tick older than 5 seconds (timestamp={timestamp}, age={time.time() - timestamp:.2f}s)"
                    )
                    await asyncio.sleep(0.001)
                    continue

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

                # NOTE: Iron Lock block removed — Supervisor directives
                # (PAUSE_ENTRIES / RESUME_ENTRIES) are now persistent
                # across ticks.  See handle_supervisor_command().

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

                    # Write to QuestDB market_features via ilp_writer
                    try:
                        from quantum_edge_core.market_data.tsdb.ilp_writer import get_ilp_writer
                        mid_val = 0.0
                        spread_val = 0.0
                        if market_state.best_ask > 0 and market_state.best_bid > 0:
                            mid_val = (market_state.best_ask + market_state.best_bid) / 2.0
                            spread_val = market_state.best_ask - market_state.best_bid
                        
                        get_ilp_writer().write_row(
                            "market_features",
                            symbols={"symbol": self.config.symbol},
                            columns={
                                "mid_price": float(mid_val),
                                "spread": float(spread_val),
                                "rsi_14": 0.0,
                                "macd_line": 0.0,
                                "macd_signal": 0.0,
                                "atr_14": float(atr_val),
                                "ofi_raw": float(feat_vec.ofi),
                                "volume_delta": float(market_state.volume_delta_1m)
                            },
                            ts=market_state.timestamp / 1000.0
                        )
                    except Exception as db_err:
                        logger.warning(f"Failed to write market features to QuestDB: {db_err}")

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

                    # ── 2.5 PaperTrader Match Check ──
                    if hasattr(self.gateway, "on_tick"):
                        from quantum_edge_core.ai_scalper_bot.bot.execution.strategy_core import TradeAction
                        filled_orders = self.gateway.on_tick(Decimal(str(market_state.last_price)))
                        for f_order in filled_orders:
                            side = f_order["side"].upper()
                            pos_side = f_order["positionSide"]
                            f_price = Decimal(str(f_order["price"]))
                            f_qty = Decimal(str(f_order["qty"]))

                            is_entry = False
                            if pos_side and pos_side.upper() == "SHORT":
                                is_entry = (side == "SELL")
                            else:
                                is_entry = (side == "BUY")

                            if is_entry:
                                action = TradeAction(
                                    action_type="ORDER_FILLED",
                                    price=f_price,
                                    qty=f_qty,
                                    reason=f"side={side}|spacing_pct=0.012",
                                )
                                logger.warning(
                                    f"🔔 PAPER MATCH DETECTED (ENTRY): {side} ({pos_side}) {f_qty} @ {f_price} | Executing counter-order..."
                                )
                                self.position.simulate_fill(
                                    f_price,
                                    f_qty,
                                    side,
                                    self.config.symbol,
                                    position_side=pos_side,
                                )
                                await self.gateway.execute(action)
                            else:
                                logger.warning(
                                    f"🔔 PAPER MATCH DETECTED (EXIT): {side} ({pos_side}) {f_qty} @ {f_price}."
                                )
                                self.position.simulate_fill(
                                    f_price,
                                    f_qty,
                                    side,
                                    self.config.symbol,
                                    position_side=pos_side,
                                )

                    # ── Decision Guards & Diagnostic Logging ──
                    reason = "Evaluating"
                    spread = market_state.best_ask - market_state.best_bid
                    action = None

                    if isinstance(market_state.last_price, Decimal):
                        limit_1pct = market_state.last_price * Decimal('0.01')
                        limit_05pct = market_state.last_price * Decimal('0.005')
                    else:
                        limit_1pct = market_state.last_price * 0.01
                        limit_05pct = market_state.last_price * 0.005

                    if spread >= 0 and spread > limit_05pct:
                        logger.warning(f'Spread warning: spread {spread:.4f} exceeds 0.5% threshold of price {market_state.last_price:.2f}')

                    if market_state.last_price <= 0.0:
                        reason = 'Waiting for Market Data (Price <= 0)'
                    elif spread < 0:
                        reason = 'Spread inverted'
                    elif spread > limit_1pct:
                        reason = f'Spread anomalous: {spread:.2f} (Bid: {market_state.best_bid}, Ask: {market_state.best_ask})'
                    elif getattr(market_state, "entries_paused", False) or getattr(self.gateway, "entries_paused", False):
                        reason = "Entries Paused by Supervisor/Policy"
                    elif atr_val <= 0.0:
                        reason = "Waiting for Volatility Data (ATR warmup)"
                    else:
                        action = self.strategy.decide(
                            market_state, feat_vec, atr_val, self.position
                        )
                        if action is None:
                            pos_val = self.position.long_state.total_qty - self.position.short_state.total_qty
                            reason = f"Strategy returned HOLD (Mode: {market_state.trading_mode.value}, OFI: {feat_vec.ofi:.2f}, Pos: {pos_val})"
                        else:
                            reason = f"Strategy returned {action.action_type}"

                    now_log = time.time()
                    if now_log - self.last_eval_log >= 10.0:
                        logger.info(
                            f"Evaluation Tick: Price={market_state.last_price:.2f}, Signal={'ENTER' if action else 'HOLD'}, Reason={reason}"
                        )
                        self.last_eval_log = now_log

                    # 4. Execution (PaperTrader — Shadow Mode)
                    active_signal_name = "HOLD"
                    if action:
                        from quantum_edge_core.ai_scalper_bot.bot.execution.smart_executor import OrderRequest

                        if isinstance(action, OrderRequest):
                            active_signal_name = f"{action.side.value}_{action.position_side.value}"
                            logger.warning(
                                f"🚀 SIGNAL GENERATED: OrderRequest {action.side.value} ({action.position_side.value}) @ {action.price} | Qty: {action.qty}"
                            )
                            self.position.simulate_fill(
                                Decimal(str(action.price)) if action.price is not None else Decimal("0.0"),
                                Decimal(str(action.qty)),
                                action.side.value,
                                self.config.symbol,
                                position_side=action.position_side,
                            )
                        else:
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
                        # Execute with timeout to prevent hanging
                        try:
                            await asyncio.wait_for(self.gateway.execute(action), timeout=90.0)
                        except asyncio.TimeoutError:
                            logger.error(f"⏱️ Gateway execute timeout (90s) for {action.action_type}")
                        except Exception as e:
                            logger.error(f"❌ Gateway execute error: {e}")

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
                # Removed inline heartbeat to prevent blocking; now handled by telemetry_loop task.

                # Yield control to event loop
                await asyncio.sleep(0.01)

        except asyncio.CancelledError:
            logger.info("Bot Engine Stopping...")
        except Exception as e:
            logger.exception(f"CRITICAL MAIN LOOP ERROR: {e}")
        finally:
            logger.info("Bot run() method completed, shutting down...")
            await self.shutdown()

    async def shutdown(self):
        logger.info("Shutting down infrastructure...")
        await self.gateway.close()
        self.reporter.close()
        self.market_stream.close()


if __name__ == "__main__":
    cleanup_old_logs()
    bot = BotEngine()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        pass
