"""Publish acknowledgements and status for quantum_edge_core.strategies.legacy.lockbot."""

from __future__ import annotations

import msgspec
import zmq

from quantum_edge_core.strategies.legacy.lockbot.lockbot.contracts.lockbot_control_v1 import (
    AckEnvelope,
    StatusEnvelope,
)
from quantum_edge_core.strategies.legacy.lockbot.lockbot.contracts.lockbot_exec_v1 import (
    ExecEnvelope,
)


class BotPublisher:
    def __init__(self, endpoint: str, sndhwm: int = 1000) -> None:
        self._ctx = zmq.Context.instance()
        self._socket = self._ctx.socket(zmq.PUB)
        self._socket.setsockopt(zmq.SNDHWM, sndhwm)
        self._socket.setsockopt(zmq.LINGER, 0)
        self._socket.bind(endpoint)

    def publish_ack(self, topic: str, ack: AckEnvelope) -> None:
        payload = msgspec.msgpack.encode(ack)
        self._socket.send_multipart([topic.encode("utf-8"), payload])

    def publish_status(self, topic: str, status: StatusEnvelope) -> None:
        payload = msgspec.msgpack.encode(status)
        self._socket.send_multipart([topic.encode("utf-8"), payload])

    def publish_exec(self, topic: str, event: ExecEnvelope) -> None:
        payload = msgspec.msgpack.encode(event)
        self._socket.send_multipart([topic.encode("utf-8"), payload])

    def close(self) -> None:
        self._socket.close()
