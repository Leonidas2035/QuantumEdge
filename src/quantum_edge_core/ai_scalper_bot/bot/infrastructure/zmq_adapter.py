"""
ZMQ Adapter for Market Data Ingestion.
Optimized for high-throughput, low-latency tick streams using ujson.
"""

import zmq
import ujson
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class ZmqSubStream:
    """
    Adapter for subscribing to ZMQ streams (MarketDataHub).
    Handles socket configuration and fast JSON decoding.
    """

    def __init__(self, endpoint: str, topic: str = "", hwm: int = 1000, connect_now: bool = True):
        """
        Args:
            endpoint: ZMQ endpoint to connect to (e.g. "tcp://127.0.0.1:5555").
            topic: Subscription topic (empty string for all).
            hwm: High Water Mark for receiving messages (buffer size).
        """
        self._context = zmq.Context()
        self._socket = self._context.socket(zmq.SUB)

        # Performance Tuning
        self._socket.setsockopt(zmq.RCVHWM, hwm)
        self._socket.setsockopt_string(zmq.SUBSCRIBE, topic)
        self.endpoint = endpoint
        self.topic = topic

        if connect_now:
            self.connect()

    def connect(self):
        try:
            self._socket.connect(self.endpoint)
            logger.info(f"Connected to ZMQ Stream at {self.endpoint} (Topic: '{self.topic}')")
        except zmq.ZMQError as e:
            logger.error(f"Failed to connect to ZMQ endpoint: {e}")
            raise

    def get_latest_tick(self, timeout_ms: int = 0) -> Optional[Dict[str, Any]]:
        """
        Retrieves the latest tick from the socket.
        Non-blocking by default or with short timeout.

        Args:
            timeout_ms: Timeout in milliseconds. 0 = non-blocking.

        Returns:
            Dict containing parsed tick data, or None if no data/error.
        """
        if timeout_ms > 0:
            if self._socket.poll(timeout_ms) == 0:
                return None

        try:
            # Using receiving flags based on timeout preference
            flags = zmq.NOBLOCK if timeout_ms == 0 else 0

            # Receive raw bytes
            # If topic included, it might be multipart [topic, payload] or single string "topic payload"
            # Assuming standard PUB/SUB where topic is stripped or part of message?
            # Usually: socket.recv_string() or recv_multipart()
            # If standard MarketDataHub sends [Topic, JSON], we'd use recv_multipart.
            # If simple JSON stream, recv().
            # Safer to try non-blocking recv.

            try:
                # MarketDataHub sends [topic, payload] multipart messages.
                frames = self._socket.recv_multipart(flags=flags)
                if not frames or len(frames) < 2:
                    return None

                msg = frames[1]

            except zmq.Again:
                return None

            # Decode payload
            try:
                decoded = ujson.loads(msg)
                return decoded

            except ujson.JSONDecodeError:
                # "If a packet is malformed, log a warning and return None"
                logger.warning("Malformed ZMQ packet received, could not decode JSON.")
                return None

        except Exception as e:
            # General safety net
            logger.error(f"Error in ZzmqSubStream: {e}")
            return None

    def close(self):
        """Cleanly close the socket."""
        self._socket.close()
        self._context.term()
