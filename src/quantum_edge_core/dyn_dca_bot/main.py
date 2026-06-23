import asyncio
import signal
import sys
import structlog
import time
import collections

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

class BotState:
    """Глобальний стан для обміну між ZMQ-потоком та торговим циклом."""
    def __init__(self):
        self.current_price: float = 0.0
        self.current_walls: Dict[str, List[Dict[str, Any]]] = {"bid_walls": [], "ask_walls": []}
        self.last_update_ts: float = 0.0

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
        self.order_router = OrderRouter() # В майбутньому прийматиме exchange client
        self.grid_manager = GridManager(self.config, self.order_router)
        
        # 4. Telemetry
        telemetry_port = self.config.zmq_telemetry_port
        self.telemetry = BotPublisher(port=telemetry_port)
        self._first_order_placed = False

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
        
        if oracle_state.regime == MarketRegime.FLASH_CRASH:
            logger.warning("FLASH CRASH detected by Oracle! Holding off new grid entries.")
            return # Блокуємо нові ордери, поки ринок не заспокоїться

        # 2. Перевірка на досягнення Take Profit
        if self.grid_manager.current_position_size > 0 and self.grid_manager.tp_price is not None:
            tp_hit = False
            current_price = self.state.current_price
            if self.grid_manager.position_side == "buy":
                if current_price >= self.grid_manager.tp_price:
                    tp_hit = True
            elif self.grid_manager.position_side == "sell":
                if current_price <= self.grid_manager.tp_price:
                    tp_hit = True
            
            if tp_hit:
                logger.info("Take Profit price target reached", current_price=current_price, tp_price=self.grid_manager.tp_price)
                self.grid_manager.on_tp_order_filled()
                self._first_order_placed = False

        # 3. Логіка виставлення першого ордера
        if self.grid_manager.current_position_size == 0 and not self._first_order_placed:
            # Рахуємо ціну з урахуванням стін
            next_price = self.dca_engine.calculate_next_order(
                current_price=self.state.current_price,
                average_entry=self.state.current_price, # Для першого ордера відштовхуємось від поточної ціни
                step_index=0,
                oracle_state=oracle_state,
                walls=self.state.current_walls,
                side="buy"
            )
            
            if next_price:
                logger.info("Placing initial grid order", price=next_price, regime=oracle_state.regime.name)
                # Імітуємо виконання (в реальності тут був би place_limit_order)
                self.grid_manager.on_dca_order_filled(fill_price=next_price, fill_qty=0.01, side="buy")
                self._first_order_placed = True

        # 4. Розрахунок PnL та Публікація телеметрії для Supervisor
        current_pnl = 0.0
        if self.grid_manager.current_position_size > 0:
            if self.grid_manager.position_side == "buy":
                current_pnl = (self.state.current_price - self.grid_manager.average_entry_price) * self.grid_manager.current_position_size
            elif self.grid_manager.position_side == "sell":
                current_pnl = (self.grid_manager.average_entry_price - self.state.current_price) * self.grid_manager.current_position_size

        if self.telemetry:
            self.telemetry.publish_status(
                position_size=self.grid_manager.current_position_size,
                avg_entry=self.grid_manager.average_entry_price,
                current_pnl=current_pnl
            )

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
