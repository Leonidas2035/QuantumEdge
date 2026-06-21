import asyncio
import signal
import sys
import structlog
import time

from typing import Optional, Dict, Any, List

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
        
        # 2. ZMQ Receiver (передаємо оракул та агрегатор всередину)
        self.zmq_receiver = ZmqReceiver(self.config, self.l2_aggregator, self.volatility_oracle)
        
        # 3. Execution
        self.order_router = OrderRouter() # В майбутньому прийматиме exchange client
        self.grid_manager = GridManager(self.config, self.order_router)
        
        # 4. Telemetry
        telemetry_port = 5567
        if hasattr(self.config, 'telemetry') and isinstance(self.config.telemetry, dict):
             telemetry_port = self.config.telemetry.get('zmq_port', 5567)
        elif hasattr(self.config, 'telemetry_port'):
             telemetry_port = self.config.telemetry_port

        self.telemetry = BotPublisher(port=telemetry_port)
        self._first_order_placed = False

    async def start(self):
        self.running = True
        
        bot_id = getattr(self.config, 'bot_id', 'dyndca_v1')
        logger.info("Starting DynDCA Microservice: Component wiring complete", bot_id=bot_id)
        
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

        # 1. Запит до Оракула: який зараз режим?
        oracle_state = self.volatility_oracle.evaluate_regime(l1_spread_bps=2.0, atr_pct=0.5) 
        
        if oracle_state.regime == MarketRegime.FLASH_CRASH:
            logger.warning("FLASH CRASH detected by Oracle! Holding off new grid entries.")
            return # Блокуємо нові ордери, поки ринок не заспокоїться

        # 2. Логіка виставлення першого ордера
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

        # 3. Публікація телеметрії для Supervisor
        if self.telemetry:
            self.telemetry.publish_status(
                position_size=self.grid_manager.current_position_size,
                avg_entry=self.grid_manager.average_entry_price,
                current_pnl=0.0 # Розрахунок PnL додамо згодом
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
