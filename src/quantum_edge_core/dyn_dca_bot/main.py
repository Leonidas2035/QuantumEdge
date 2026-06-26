import asyncio
import signal
import sys
import structlog
import time
import collections
import os
from pathlib import Path

from dotenv import load_dotenv

from typing import Optional, Dict, Any, List

from quantum_edge_core.logging_setup import setup_logging
from quantum_edge_core.dyn_dca_bot.core.config import DynDCAConfig
from quantum_edge_core.dyn_dca_bot.market_data.l2_aggregator import L2Aggregator
from quantum_edge_core.dyn_dca_bot.market_data.zmq_receiver import ZmqReceiver
from quantum_edge_core.dyn_dca_bot.strategy.volatility_oracle import EnterpriseVolatilityOracle, MarketRegime
from quantum_edge_core.dyn_dca_bot.strategy.dca_engine import DCAEngine
from quantum_edge_core.dyn_dca_bot.execution.order_router import OrderRouter
from quantum_edge_core.dyn_dca_bot.execution.grid_manager import GridManager
from quantum_edge_core.dyn_dca_bot.execution.telemetry import BotPublisher

logger = structlog.get_logger(__name__)

_SECRETS_PATH = Path(__file__).resolve().parents[3] / "config" / "secrets.local.env"


def _load_secrets() -> None:
    if _SECRETS_PATH.exists():
        load_dotenv(dotenv_path=str(_SECRETS_PATH), override=False)
        logger.info("Loaded secrets", path=str(_SECRETS_PATH))
    else:
        logger.warning("Secrets file not found", path=str(_SECRETS_PATH))


if __name__ == "__main__":
    _load_secrets()
    setup_logging()


class BotState:
    """Глобальний стан для обміну між ZMQ-потоком та торговим циклом."""
    def __init__(self):
        self.current_price: float = 0.0
        self.current_walls: Dict[str, List[Dict[str, Any]]] = {"bid_walls": [], "ask_walls": []}
        self.last_update_ts: float = 0.0
        self.active_grid_orders: Dict[str, Dict[str, Any]] = {}


class DynDCAService:
    def __init__(self):
        self.config = DynDCAConfig.load()
        self.running = False
        self.state = BotState()
        
        # --- ІНІЦІАЛІЗАЦІЯ МОДУЛІВ (DEPENDENCY INJECTION) ---
        
        # 1. Market Data & Strategy
        wall_threshold = 10.0
        grid_spacing = 0.5
        gamma = 1.2
        
        if hasattr(self.config, 'strategy') and isinstance(self.config.strategy, dict):
            wall_threshold = self.config.strategy.get('wall_detection_threshold', 10.0)
            grid_spacing = self.config.strategy.get('grid_spacing_pct', 0.5)
            gamma = self.config.strategy.get('gamma', 1.2)
        else:
            if hasattr(self.config, 'wall_detection_threshold'):
                wall_threshold = self.config.wall_detection_threshold
            if hasattr(self.config, 'grid_spacing_pct'):
                grid_spacing = self.config.grid_spacing_pct
            if hasattr(self.config, 'gamma'):
                gamma = self.config.gamma

        self.l2_aggregator = L2Aggregator(
            max_depth_pct=5.0, 
            wall_multiplier=wall_threshold
        )
        self.volatility_oracle = EnterpriseVolatilityOracle()
        self.dca_engine = DCAEngine(
            grid_spacing_pct=grid_spacing,
            gamma=gamma
        )
        
        # 2. ZMQ Receiver (передаємо оракул та агрегатор всередину, відкладаємо з'єднання)
        self.zmq_receiver = ZmqReceiver(self.config, self.l2_aggregator, self.volatility_oracle, connect_now=False)
        
        # 3. Execution
        self.order_router = OrderRouter()
        self._grid_managers: Dict[str, GridManager] = {}
        
        # 4. Telemetry
        telemetry_port = self.config.zmq_telemetry_port
        self.telemetry = BotPublisher(port=telemetry_port)
        self._initialized = False

    async def warm_up(self):
        self.volatility_history = collections.deque(maxlen=1000)
        self.price_history = collections.deque(maxlen=1000)
        symbol = getattr(self.config, 'symbol', 'BTCUSDT')
        logger.info("Fetching historical volatility data from QuestDB for warm-up...", symbol=symbol)
        try:
            from quantum_edge_core.market_data.tsdb.query_builder import QuestDBQueryBuilder
            db = QuestDBQueryBuilder()
            history = await db.get_volatility_profile(symbol, hours=4)
            logger.info("Received volatility profile from QuestDB. Appending to deques...", count=len(history))
            for record in history:
                self.volatility_history.append(record.get('atr_14', 0.0))
                self.price_history.append(record.get('mid_price', 0.0))
            logger.info("Warm-up complete.", volatility_len=len(self.volatility_history), price_len=len(self.price_history))
        except Exception as e:
            logger.error("Error during warm-up", error=str(e))

    async def start(self):
        self.running = True
        
        bot_id = getattr(self.config, 'bot_id', 'dyndca_v1')
        logger.info("Starting DynDCA Microservice: Component wiring complete", bot_id=bot_id)
        
        await self.warm_up()
        self.zmq_receiver.connect()
        
        # Запускаємо ZMQ слухача як фонове завдання
        receiver_task = asyncio.create_task(self.zmq_receiver.start_listening(self.state))
        
        try:
            while self.running:
                await self._trading_loop_cycle()
                await asyncio.sleep(1)  # Цикл прийняття рішень кожну секунду
                
        except asyncio.CancelledError:
            logger.warning("Main loop cancelled")
        finally:
            receiver_task.cancel()
            await self.shutdown()

    async def _trading_loop_cycle(self):
        """Ізольований цикл оцінки ринку та управління сіткою."""
        if self.state.current_price <= 0:
            logger.debug("Waiting for market data...")
            return

        # STALE_DATA check: reject processing if data is older than 5 seconds
        if self.state.last_update_ts > 0 and (time.time() - self.state.last_update_ts) > 5.0:
            logger.warning("STALE_DATA: Market data is older than 5 seconds. Skipping trading loop cycle.")
            return

        # 1. Запит до Оракула: який зараз режим?
        oracle_state = self.volatility_oracle.evaluate_regime(l1_spread_bps=2.0, atr_pct=0.5) 
        
        if oracle_state.regime == MarketRegime.FLASH_CRASH and self._initialized:
            logger.warning("FLASH CRASH detected by Oracle! Holding off new grid entries.")
            return # Блокуємо нові ордери, поки ринок не заспокоїться

        # 2. Первинна ініціалізація сітки (одноразова)
        if not self._initialized and not self.state.active_grid_orders:
            await self._initialize_grid(oracle_state)
            self._initialized = True

        # 3. Оновлення стану активних позицій (TP/закриття)
        await self._update_grid_positions()

        # 4. Розрахунок PnL та Публікація телеметрії
        total_pnl = self._calculate_total_pnl()
        active_count = len(self.state.active_grid_orders)
        if self.telemetry:
            self.telemetry.publish_status(
                position_size=active_count,
                avg_entry=0.0,
                current_pnl=total_pnl
            )

    async def _initialize_grid(self, oracle_state):
        """Places grid orders based on capital allocation and market regime."""
        current_price = self.state.current_price
        max_orders = getattr(self.config, 'max_orders_per_side', 15)

        # Calculate order size per side
        total_capital = getattr(self.config, 'total_capital_vst', 150000.0)
        order_value = max(total_capital / (max_orders * 2), getattr(self.config, 'min_order_value_vst', 500.0))
        qty = round(order_value / current_price, 4)

        regime = getattr(oracle_state, 'regime', None)
        allow_long = regime in (MarketRegime.CALM,)
        allow_short = regime in (MarketRegime.CHOPPY, MarketRegime.FLASH_CRASH)

        logger.info("Initializing grid", price=current_price, qty=qty, order_value=order_value, sides=max_orders, regime=str(regime))

        if allow_long:
            # Place long orders (buy) below current price
            for i in range(max_orders):
                side = "buy"
                next_price = self.dca_engine.calculate_next_order(
                    current_price=current_price,
                    average_entry=current_price,
                    step_index=i,
                    oracle_state=oracle_state,
                    walls=self.state.current_walls,
                    side=side
                )
                if not next_price:
                    continue
                order = self.order_router.place_limit_order(side=side, price=next_price, qty=qty, reduce_only=False, position_side='LONG')
                if order and order.get("order_id"):
                    mgr = GridManager(self.config, self.order_router)
                    mgr.on_dca_order_filled(fill_price=next_price, fill_qty=qty, side=side)
                    self.state.active_grid_orders[order["order_id"]] = {
                        "side": side,
                        "qty": qty,
                        "entry": next_price,
                        "mgr": mgr,
                        "bin_side": "long",
                    }
                    self._grid_managers[order["order_id"]] = mgr
                    logger.info("Grid long order placed", index=i, price=next_price, order_id=order["order_id"])

        if allow_short:
            # Place short orders (sell) above current price
            for i in range(max_orders):
                side = "sell"
                next_price = self.dca_engine.calculate_next_order(
                    current_price=current_price,
                    average_entry=current_price,
                    step_index=i,
                    oracle_state=oracle_state,
                    walls=self.state.current_walls,
                    side=side
                )
                if not next_price:
                    continue
                order = self.order_router.place_limit_order(side=side, price=next_price, qty=qty, reduce_only=False, position_side='SHORT')
                if order and order.get("order_id"):
                    mgr = GridManager(self.config, self.order_router)
                    mgr.on_dca_order_filled(fill_price=next_price, fill_qty=qty, side=side)
                    self.state.active_grid_orders[order["order_id"]] = {
                        "side": side,
                        "qty": qty,
                        "entry": next_price,
                        "mgr": mgr,
                        "bin_side": "short",
                    }
                    self._grid_managers[order["order_id"]] = mgr
                    logger.info("Grid short order placed", index=i, price=next_price, order_id=order["order_id"])

    async def _update_grid_positions(self):
        """Simplified: do not close individual grid positions early.
        In production, this should query the exchange for actual fills and manage TP/SL at position level."""
        current_price = self.state.current_price
        closed = []
        for order_id, pos in list(self.state.active_grid_orders.items()):
            entry = pos["entry"]
            side = pos["side"]
            tp_hit = False
            # Do NOT close long positions at a loss during downtrends
            # Only close if clearly profitable after fees
            if side == "buy" and current_price >= entry * 1.005:
                tp_hit = True
            elif side == "sell" and current_price <= entry * 0.995:
                tp_hit = True

            if tp_hit:
                mgr = self._grid_managers.get(order_id)
                if mgr:
                    mgr.on_tp_order_filled()
                closed.append(order_id)
                logger.info("Grid position closed", order_id=order_id, side=side, entry=entry, price=current_price)

        for oid in closed:
            self.state.active_grid_orders.pop(oid, None)
            self._grid_managers.pop(oid, None)
            # Only re-init if all grid orders are gone
            if not self.state.active_grid_orders:
                self._initialized = False

    def _calculate_total_pnl(self) -> float:
        total = 0.0
        cp = self.state.current_price
        for pos in self.state.active_grid_orders.values():
            e = pos["entry"]
            s = pos["side"]
            q = pos["qty"]
            if s == "buy":
                total += (cp - e) * q
            elif s == "sell":
                total += (e - cp) * q
        return total

    async def shutdown(self):
        logger.info("Initiating graceful shutdown for DynDCA...")
        self.running = False
        self.zmq_receiver.close()
        if self.telemetry:
            self.telemetry.close()
        logger.info("Shutdown complete.")

def handle_exception(loop, context):
    msg = context.get("exception", context["message"])
    logger.error("Caught exception", error=str(msg))

if __name__ == "__main__":
    setup_logging()
    
    service = DynDCAService()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(service.shutdown()))
        except NotImplementedError:
            pass

    loop.set_exception_handler(handle_exception)
    
    try:
        loop.run_until_complete(service.start())
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received.")
    
    loop.run_until_complete(service.shutdown())
