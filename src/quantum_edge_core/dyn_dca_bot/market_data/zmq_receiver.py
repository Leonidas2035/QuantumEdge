import zmq
import zmq.asyncio
import json
import asyncio
import structlog
from typing import Any, Dict

logger = structlog.get_logger(__name__)

class ZmqReceiver:
    def __init__(self, config: Any, l2_aggregator: Any, volatility_oracle: Any):
        self.config = config
        self.l2_aggregator = l2_aggregator
        self.volatility_oracle = volatility_oracle

        self.context = zmq.asyncio.Context()
        self.socket = self.context.socket(zmq.SUB)
        
        # Безпечний витяг порту (бо config це dataclass DynDCAConfig, а не словник)
        port = getattr(self.config, 'zmq_market_data_port', 5555)
        self.zmq_url = f"tcp://127.0.0.1:{port}"
        self.socket.connect(self.zmq_url)

        # ВИПРАВЛЕННЯ 1: Використовуємо стандарти топіків MarketDataHub (маленькі літери, крапка)
        topics = ["depth.btcusdt", "trade.btcusdt"]
        for topic in topics:
            self.socket.setsockopt_string(zmq.SUBSCRIBE, topic)
            logger.info("Subscribed to ZMQ topic", topic=topic)

        self.last_trade_price = 0.0

    async def start_listening(self, state_manager: Any):
        logger.info("ZMQ Receiver started listening", endpoint=self.zmq_url)
        
        while True:
            try:
                # ВИПРАВЛЕННЯ 2: Читання multipart повідомлень замість recv_string()
                frames = await self.socket.recv_multipart()
                
                if len(frames) < 2:
                    continue
                    
                topic = frames[0].decode('utf-8')
                payload_str = frames[1].decode('utf-8')
                payload = json.loads(payload_str)

                # ВИПРАВЛЕННЯ 3: Маршрутизація за новим форматом топіка
                if topic.startswith("trade"):
                    self._handle_trade(payload, state_manager)
                elif topic.startswith("depth"):
                    self._handle_l2(payload, state_manager)

            except asyncio.CancelledError:
                logger.info("ZMQ Receiver shutting down...")
                break
            except Exception as e:
                logger.error("Error processing ZMQ message", error=str(e), topic=topic if 'topic' in locals() else 'unknown')

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
