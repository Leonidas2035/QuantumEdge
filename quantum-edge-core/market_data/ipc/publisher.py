"""ZeroMQ PUB socket wrapper for MarketDataHub."""

from __future__ import annotations

import logging

import zmq

from market_data.config import HubConfig
from market_data.models import MarketEvent, encode_event


class ZmqPublisher:
    """Publishes market events over ZeroMQ using IPC endpoints."""

    def __init__(self, config: HubConfig) -> None:
        self._config = config
        self._ctx = zmq.Context.instance()
        self._socket = self._ctx.socket(zmq.PUB)
        self._socket.setsockopt(zmq.SNDHWM, config.zmq.snd_hwm)
        self._socket.setsockopt(zmq.LINGER, 0)
        if config.zmq.conflate_l1:
            self._socket.setsockopt(zmq.CONFLATE, 1)
        self._socket.bind(config.zmq.endpoint)
        logging.info("ZMQ publisher bound to %s", config.zmq.endpoint)

    @staticmethod
    def topic_for_event(event: MarketEvent) -> bytes:
        return f"{event.symbol}:{event.event_type}".encode("utf-8")

    def publish(self, event: MarketEvent) -> None:
        topic = self.topic_for_event(event)
        payload = encode_event(event)
        self._socket.send_multipart([topic, payload])
        logging.debug("Published event %s seq=%s topic=%s", event.event_type, event.seq, topic)

    def publish_payload(self, topic: str, payload: bytes) -> None:
        self._socket.send_multipart([topic.encode("utf-8"), payload])

    def close(self) -> None:
        self._socket.close()
        logging.info("ZMQ publisher closed")
