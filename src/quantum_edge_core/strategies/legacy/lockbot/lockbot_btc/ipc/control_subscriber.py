"""Supervisor control subscriber for quantum_edge_core.strategies.legacy.lockbot."""

from __future__ import annotations

import asyncio
import contextlib
from typing import AsyncIterator, Optional

import msgspec
import zmq
import zmq.asyncio

from quantum_edge_core.strategies.legacy.lockbot.lockbot.contracts.lockbot_control_v1 import (
    CommandEnvelope,
)


class ControlSubscriber:
    def __init__(self, endpoint: str, topic: str, rcvhwm: int = 1000) -> None:
        self._endpoint = endpoint
        self._topic = topic
        self._ctx = zmq.asyncio.Context.instance()
        self._socket: Optional[zmq.asyncio.Socket] = None
        self._queue: asyncio.Queue[CommandEnvelope] = asyncio.Queue()
        self._stop = asyncio.Event()
        self._reader_task: Optional[asyncio.Task] = None
        self._rcvhwm = rcvhwm

    async def start(self) -> None:
        if self._socket:
            return
        self._socket = self._ctx.socket(zmq.SUB)
        self._socket.setsockopt(zmq.RCVHWM, self._rcvhwm)
        self._socket.setsockopt(zmq.LINGER, 0)
        self._socket.setsockopt(zmq.SUBSCRIBE, self._topic.encode("utf-8"))
        self._socket.connect(self._endpoint)
        self._stop.clear()
        self._reader_task = asyncio.create_task(self._reader_loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._reader_task:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task
        if self._socket:
            self._socket.close()
        self._socket = None

    async def commands(self) -> AsyncIterator[CommandEnvelope]:
        while True:
            item = await self._queue.get()
            yield item

    async def _reader_loop(self) -> None:
        while not self._stop.is_set():
            try:
                _topic, payload = await self._socket.recv_multipart()  # type: ignore[union-attr]
            except asyncio.CancelledError:
                break
            except Exception:
                continue
            try:
                cmd = msgspec.msgpack.decode(payload, type=CommandEnvelope)
            except Exception:
                continue
            self._queue.put_nowait(cmd)
