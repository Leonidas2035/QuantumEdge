"""
src/quantum_edge_core/market_data/ipc/subscriber.py

Reusable ZMQ Subscriber for IPC.
"""

import zmq
import zmq.asyncio
import structlog
from typing import Optional

from quantum_edge_core.events import BaseEvent, EventCodec

logger = structlog.get_logger()

class ZmqSubscriber:
    """
    Async ZMQ Subscriber.
    Consumes events from MarketDataHub.
    """
    def __init__(self, host: str = "127.0.0.1", port: int = 5555):
        self.endpoint = f"tcp://{host}:{port}"
        self.ctx = zmq.asyncio.Context()
        self.socket = self.ctx.socket(zmq.SUB)
        self.logger = logger.bind(component="ZmqSubscriber", endpoint=self.endpoint)
        self.connected = False

    def connect(self, topics: list[str] = None):
        if self.connected:
            return
        
        self.logger.info("Connecting to Market Buffer")
        self.socket.connect(self.endpoint)
        
        if topics is None:
            topics = [""] # Subscribe all
            
        for topic in topics:
            self.socket.subscribe(topic)
            self.logger.info("Subscribed to topic", topic=topic or "ALL")
            
        self.connected = True

    async def next_event(self) -> Optional[BaseEvent]:
        """
        Wait for and decode the next event.
        Returns None if decode fails, but conventionally users call this in a loop.
        """
        try:
            # format: [topic, payload]
            msg = await self.socket.recv_multipart()
            if len(msg) < 2:
                return None
            
            payload = msg[1]
            try:
                event = EventCodec.decode(payload)
                return event
            except Exception:
                # Log but don't crash
                # self.logger.debug("Decode failed", error=str(e))
                return None
                
        except zmq.ZMQError as e:
            self.logger.error("ZMQ Error", error=str(e))
            raise e
        except Exception as e:
            self.logger.error("Subscriber Error", error=str(e))
            raise e

    def close(self):
        self.socket.close()
        self.ctx.term()
