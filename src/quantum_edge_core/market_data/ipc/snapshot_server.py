"""REQ/REP snapshot endpoint for MarketDataHub."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Deque, Dict

import msgspec
import zmq
import zmq.asyncio

from quantum_edge_core.market_data.config import HubConfig
from quantum_edge_core.market_data.models import (
    Bar1sEvent,
    DepthL2Event,
    L1Event,
    MarketEvent,
    SnapshotRequest,
    SnapshotResponse,
    TradeEvent,
    WallsEvent,
    encode_event,
)


class SnapshotCache:
    def __init__(self, trade_tail: int = 0) -> None:
        self._l1: Dict[str, L1Event] = {}
        self._bar: Dict[str, Bar1sEvent] = {}
        self._trades: Deque[TradeEvent] = deque(maxlen=trade_tail)
        self._depth: Dict[str, DepthL2Event] = {}
        self._walls: Dict[str, WallsEvent] = {}

    def update(self, event: MarketEvent) -> None:
        if isinstance(event, L1Event):
            self._l1[event.symbol] = event
        elif isinstance(event, Bar1sEvent):
            self._bar[event.symbol] = event
        elif isinstance(event, TradeEvent):
            self._trades.append(event)
        elif isinstance(event, DepthL2Event):
            self._depth[event.symbol] = event
        elif isinstance(event, WallsEvent):
            self._walls[event.symbol] = event

    def snapshot_for(self, symbol: str, event_type: str) -> MarketEvent | None:
        if event_type == "l1":
            return self._l1.get(symbol)
        if event_type == "bar1s":
            return self._bar.get(symbol)
        if event_type == "trade_tail":
            return list(self._trades)
        if event_type == "depth_l2":
            return self._depth.get(symbol)
        if event_type == "walls":
            return self._walls.get(symbol)
        return None


class SnapshotServer:
    def __init__(self, config: HubConfig, cache: SnapshotCache) -> None:
        self._config = config
        self._cache = cache
        self._ctx = zmq.asyncio.Context.instance()
        self._socket = self._ctx.socket(zmq.REP)
        self._socket.setsockopt(zmq.LINGER, 0)
        self._socket.bind(self._config.snapshot.endpoint)
        self._task: asyncio.Task | None = None
        self._running = False

    def start(self) -> None:
        if not self._running:
            self._running = True
            self._task = asyncio.create_task(self._loop())
            logging.info("Snapshot server bound to %s", self._config.snapshot.endpoint)

    def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        self._socket.close()

    async def _loop(self) -> None:
        while self._running:
            try:
                raw = await self._socket.recv()
                request = msgspec.msgpack.decode(raw, type=SnapshotRequest)
                response = self._handle_request(request)
                await self._socket.send(msgspec.msgpack.encode(response))
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logging.exception("Snapshot server error: %s", exc)
                await self._socket.send(msgspec.msgpack.encode(SnapshotResponse(False, time.time_ns(), "", b"", note=str(exc))))

    def _handle_request(self, request: SnapshotRequest) -> SnapshotResponse:
        now = time.time_ns()
        snapshot = self._cache.snapshot_for(request.symbol, request.event_type)
        if snapshot is None:
            return SnapshotResponse(False, now, request.event_type, b"", note="no snapshot")
        if isinstance(snapshot, list):
            payload = b""
            note = "trade tail not implemented"
            return SnapshotResponse(False, now, request.event_type, payload, note=note)
        payload = encode_event(snapshot)
        return SnapshotResponse(True, now, request.event_type, payload)
