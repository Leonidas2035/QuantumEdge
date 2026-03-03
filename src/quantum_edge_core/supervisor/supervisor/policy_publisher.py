"""ZMQ PUB publisher for Supervisor → LockBot policy directives.

Publishes `directive.v1` messages on ZMQ PUB socket.
LockBot subscribes via ControlSubscriber on this same port.

Usage:
    publisher = ZmqPolicyPublisher("tcp://*:5556")
    await publisher.start()
    await publisher.publish_directive("LONG_ONLY", 0.8, "4H uptrend, safe equity")
    publisher.close()
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Optional

import zmq
import zmq.asyncio

logger = logging.getLogger(__name__)


class ZmqPolicyPublisher:
    """Publishes LLM Supervisor directives to LockBot via ZMQ PUB."""

    TOPIC = "LOCKBOT:BTCUSDT:directive"

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

    async def publish_directive(
        self,
        mode: str,
        risk_multiplier: float,
        reasoning: str,
    ) -> None:
        """Publish a directive.v1 message to all subscribers."""
        if self._socket is None:
            logger.warning("Cannot publish: socket not started.")
            return

        payload = {
            "schema": "directive.v1",
            "ts_ms": int(time.time() * 1000),
            "policy_id": str(uuid.uuid4()),
            "mode": mode,
            "risk_multiplier": risk_multiplier,
            "reasoning": reasoning,
        }

        topic_bytes = self._topic.encode("utf-8")
        payload_bytes = json.dumps(payload).encode("utf-8")
        await self._socket.send_multipart([topic_bytes, payload_bytes])

        logger.info(
            "[ZMQ PUB] Directive sent: mode=%s risk=%.2f id=%s",
            mode,
            risk_multiplier,
            payload["policy_id"][:8],
        )

    def close(self) -> None:
        """Close PUB socket."""
        if self._socket:
            self._socket.close()
            self._socket = None
            logger.info("ZmqPolicyPublisher closed.")
