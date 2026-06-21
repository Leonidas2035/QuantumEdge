import zmq
import zmq.asyncio
import json
import asyncio
import structlog
from typing import Any, Dict

logger = structlog.get_logger(__name__)

class ZmqReceiver:
    def __init__(self, config: Any, l2_aggregator: Any, volatility_oracle: Any):
        """
        :param config: Об'єкт конфігурації DynDCAConfig
        :param l2_aggregator: Інстанс L2Aggregator для пошуку стін
        :param volatility_oracle: Інстанс EnterpriseVolatilityOracle для оновлення EWMA
        """
        self.config = config
        self.l2_aggregator = l2_aggregator
        self.volatility_oracle = volatility_oracle

        self.context = zmq.asyncio.Context()
        self.socket = self.context.socket(zmq.SUB)
        
        # Використовуємо порт з конфігурації (в залежності від версії config може бути як dict або об'єкт)
        port = 5555
        if hasattr(self.config, 'market_data_port'):
            port = self.config.market_data_port
        elif isinstance(self.config, dict) and 'market_data' in self.config:
            port = self.config['market_data'].get('zmq_port', 5555)

        self.zmq_url = f"tcp://127.0.0.1:{port}"
        self.socket.connect(self.zmq_url)

        # Підписка на топіки (trade та depth_l2)
        topics = []
        if isinstance(self.config, dict) and 'market_data' in self.config:
             topics = self.config['market_data'].get('topics', [])
        else:
             # Default topics якщо ми не можемо витягти їх з конфігу напряму
             topics = ["BTCUSDT:depth_l2", "BTCUSDT:trade"]

        for topic in topics:
            self.socket.setsockopt_string(zmq.SUBSCRIBE, topic)
            logger.info("Subscribed to ZMQ topic", topic=topic)

        # Стан для Оракула
        self.last_trade_price = 0.0

    async def start_listening(self, state_manager: Any):
        """
        Головний асинхронний цикл прослуховування.
        :param state_manager: Об'єкт, що зберігає поточний стан бота (стіни, поточна ціна)
        """
        logger.info("ZMQ Receiver started listening", endpoint=self.zmq_url)
        
        while True:
            try:
                # Чекаємо на повідомлення від MarketDataHub
                message = await self.socket.recv_string()
                topic, payload_str = message.split(" ", 1)
                payload = json.loads(payload_str)

                if topic.endswith("trade"):
                    self._handle_trade(payload, state_manager)
                elif topic.endswith("depth_l2"):
                    self._handle_l2(payload, state_manager)

            except asyncio.CancelledError:
                logger.info("ZMQ Receiver shutting down...")
                break
            except Exception as e:
                logger.error("Error processing ZMQ message", error=str(e))

    def _handle_trade(self, payload: Dict[str, Any], state_manager: Any):
        # Отримуємо ціну з тіку
        current_price = float(payload.get("price", 0.0))
        if current_price <= 0:
            return

        # Оновлюємо EWMA дисперсію в Оракулі (якщо маємо попередню ціну)
        if self.last_trade_price > 0:
            self.volatility_oracle.update_tick(current_price, self.last_trade_price)

        # Зберігаємо поточну ціну для наступного тіку та оновлюємо глобальний стан
        self.last_trade_price = current_price
        
        # Перевірка наявності атрибуту, бо state_manager може бути просто dataclass
        if hasattr(state_manager, 'current_price'):
            state_manager.current_price = current_price

    def _handle_l2(self, payload: Dict[str, Any], state_manager: Any):
        # Знаходимо стіни та оновлюємо глобальний стан для DCA Engine (Magnet Effect)
        walls = self.l2_aggregator.analyze_orderbook(payload)
        
        if hasattr(state_manager, 'current_walls'):
            state_manager.current_walls = walls

    def close(self):
        self.socket.close()
        self.context.term()
