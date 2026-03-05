"""ZMQ PUB publisher for Supervisor → Bot policy directives.

Publishes ``TradingPolicy`` as MessagePack on ZMQ PUB socket.
Bot subscribes via SUB on the same port.

Usage::

    publisher = ZmqPolicyPublisher("tcp://*:5556")
    await publisher.start()
    await publisher.publish_policy(policy)
    publisher.close()
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import msgspec.msgpack
import zmq
import zmq.asyncio

from quantum_edge_core.shared.trading_policy import TradingPolicy

logger: logging.Logger = logging.getLogger(__name__)


class ZmqPolicyPublisher:
    """Publishes TradingPolicy via MessagePack on ZMQ PUB."""

    TOPIC: str = "policy.v2"

    def __init__(
        self,
        bind_url: str = "tcp://*:5556",
        topic: Optional[str] = None,
    ) -> None:
        self._bind_url = bind_url
        self._topic = topic or self.TOPIC
        self._ctx = zmq.asyncio.Context.instance()
        self._socket: Optional[zmq.asyncio.Socket] = None

    async def start(self) -> None:
        """Bind PUB socket. Call once before publishing."""
        if self._socket is not None:
            return
        self._socket = self._ctx.socket(zmq.PUB)
        self._socket.setsockopt(zmq.LINGER, 0)
        self._socket.setsockopt(zmq.SNDHWM, 100)
        self._socket.bind(self._bind_url)
        logger.info(
            "ZmqPolicyPublisher bound on %s (topic=%s)", self._bind_url, self._topic
        )

    async def publish_policy(self, policy: TradingPolicy) -> None:
        """Publish a TradingPolicy as MessagePack."""
        if self._socket is None:
            logger.warning("Cannot publish: socket not started.")
            return

        payload_bytes: bytes = msgspec.msgpack.encode(policy)
        topic_bytes: bytes = self._topic.encode("utf-8")
        await self._socket.send_multipart([topic_bytes, payload_bytes])

        logger.info(
            "[ZMQ PUB] Policy sent: mode=%s risk=%.2f buy_max=%.2f sell_min=%.2f",
            policy.strategy_mode,
            policy.risk_multiplier,
            policy.buy_zone_max,
            policy.sell_zone_min,
        )

    async def publish_directive(
        self,
        mode: str,
        risk_multiplier: float,
        reasoning: str,
        buy_zone_max: float = 0.0,
        sell_zone_min: float = 0.0,
    ) -> None:
        """Backward-compatible wrapper: builds TradingPolicy and publishes."""
        policy = TradingPolicy(
            timestamp=time.time(),
            strategy_mode=mode,
            risk_multiplier=risk_multiplier,
            buy_zone_max=buy_zone_max,
            sell_zone_min=sell_zone_min,
            reasoning=reasoning,
        )
        await self.publish_policy(policy)

    def close(self) -> None:
        """Close PUB socket."""
        if self._socket:
            self._socket.close()
            self._socket = None
            logger.info("ZmqPolicyPublisher closed.")
