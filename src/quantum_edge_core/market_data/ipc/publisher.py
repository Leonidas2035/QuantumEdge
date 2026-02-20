"""
src/quantum_edge_core/market_data/ipc/publisher.py

ZeroMQ Publisher using msgspec serialization.
"""

import zmq
import zmq.asyncio
import structlog
from quantum_edge_core.events import BaseEvent, EventCodec

logger = structlog.get_logger()


class ZmqPublisher:
    """
    Async ZeroMQ Publisher for broadcasting Market Data events.
    """

    def __init__(self, port: int = 5555):
        self.port = port
        self.logger = logger.bind(component="ZmqPublisher", port=port)

        self.ctx = zmq.asyncio.Context()
        self.socket = self.ctx.socket(zmq.PUB)
        self.socket.setsockopt(zmq.RCVTIMEO, 2000)
        self.socket.setsockopt(zmq.SNDTIMEO, 2000)

        # Don't wait for unsent messages on shutdown
        self.socket.setsockopt(zmq.LINGER, 0)

        try:
            self.socket.bind(f"tcp://*:{port}")
            self.logger.info("ZMQ Publisher bound")
        except zmq.ZMQError as e:
            self.logger.error("Failed to bind ZMQ socket", error=str(e))
            raise

    async def publish(self, topic: str, event: BaseEvent):
        """
        Serialize and broadcast an event.
        Failures are logged but do not propagate exceptions to avoid crashing the caller.
        """
        try:
            payload = EventCodec.encode(event)
            topic_bytes = topic.encode("utf-8")

            # Send topic and payload as multipart message
            await self.socket.send_multipart([topic_bytes, payload])

        except Exception as e:
            self.logger.error("Failed to publish event", topic=topic, error=str(e))

    def publish_payload(self, topic: str, payload: bytes) -> None:
        """
        Broadcast a raw pre-encoded payload (used by AccountPublisher).
        """
        try:
            topic_bytes = topic.encode("utf-8")
            # AccountPublisher is synchronous but ZmqPublisher is asyncio based. 
            # We must use the underlying ZMQ non-blocking send or schedule it.
            # However ZmqPublisher.socket is AsyncSocket, so send_multipart expects await.
            # To fix sync/async bridge, we can either use send_multipart(..., flags=zmq.NOBLOCK)
            self.socket.send_multipart([topic_bytes, payload], flags=zmq.NOBLOCK)
        except Exception as e:
            self.logger.error("Failed to publish raw payload", topic=topic, error=str(e))

    async def stop(self):
        """
        Gracefully close socket and context.
        """
        self.logger.info("Stopping ZMQ Publisher")
        self.socket.close()
        self.ctx.term()
