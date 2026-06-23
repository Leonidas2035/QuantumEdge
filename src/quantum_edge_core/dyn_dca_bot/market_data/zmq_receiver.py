import zmq
import zmq.asyncio
import json
import asyncio
import time
import structlog
from typing import Any, Dict

logger = structlog.get_logger(__name__)

class ZmqReceiver:
    def __init__(self, config: Any, l2_aggregator: Any, volatility_oracle: Any, connect_now: bool = True):
        self.config = config
        self.l2_aggregator = l2_aggregator
        self.volatility_oracle = volatility_oracle

        self.context = zmq.asyncio.Context()
        self.socket = self.context.socket(zmq.SUB)
        
        # Безпечний витяг порту (бо config це dataclass DynDCAConfig, а не словник)
        port = getattr(self.config, 'zmq_market_data_port', 5555)
        self.zmq_url = f"tcp://127.0.0.1:{port}"

        # ВИПРАВЛЕННЯ 1: Використовуємо стандарти топіків MarketDataHub (маленькі літери, крапка)
        self.topics = ["depth.btcusdt", "trade.btcusdt"]
        for topic in self.topics:
            self.socket.setsockopt_string(zmq.SUBSCRIBE, topic)

        self.last_trade_price = 0.0
        if connect_now:
            self.connect()

    def connect(self):
        self.socket.connect(self.zmq_url)
        for topic in self.topics:
            logger.info("Subscribed and connected to ZMQ topic", topic=topic)

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

                # Extract and normalize timestamp to seconds
                ts_raw = payload.get("timestamp") or payload.get("T") or payload.get("ts_ns") or 0
                ts = float(ts_raw)
                if ts > 1e18:
                    ts /= 1e9  # ns to s
                elif ts > 1e12:
                    ts /= 1e3  # ms to s

                if ts > 0 and (time.time() - ts) > 5.0:
                    logger.warning("STALE_DATA: Rejecting message older than 5 seconds", topic=topic, timestamp=ts, age=time.time() - ts)
                    continue

                if hasattr(state_manager, 'last_update_ts'):
                    state_manager.last_update_ts = ts if ts > 0 else time.time()

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
        # Extract true mid_price from Hub
        mid_price = payload.get("mid_price")
        if mid_price is not None and mid_price > 0:
            if hasattr(state_manager, 'current_price'):
                state_manager.current_price = float(mid_price)
                
        # Знаходимо стіни та оновлюємо глобальний стан для DCA Engine (Magnet Effect)
        walls = self.l2_aggregator.analyze_orderbook(payload)
        
        if hasattr(state_manager, 'current_walls'):
            state_manager.current_walls = walls

    def close(self):
        self.socket.close()
        self.context.term()
