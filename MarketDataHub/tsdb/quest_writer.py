"""QuestDB ILP writer with micro-batch buffering for warm path."""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
import socket
import time
from typing import Dict, Iterable, List, Optional

from MarketDataHub.config import TsdbConfig
from MarketDataHub.models import Bar1sEvent, L1Event, MarketEvent


@dataclasses.dataclass
class TsdbMetrics:
    written_rows: int = 0
    dropped_rows: int = 0
    last_flush_ts: Optional[float] = None
    errors: int = 0


class QuestILPWriter:
    """Micro-batch ILP writer with bounded caches."""

    def __init__(self, config: TsdbConfig) -> None:
        self._config = config
        self._l1_cache: Dict[str, L1Event] = {}
        self._bars_queue: asyncio.Queue[Bar1sEvent] = asyncio.Queue(maxsize=self._config.bars_queue_max)
        self._batch_rows = self._config.batch_rows
        self._flush_interval = self._config.flush_interval_ms / 1000.0
        self._metrics = TsdbMetrics()
        self._task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._writer: Optional[asyncio.StreamWriter] = None
        self._reader: Optional[asyncio.StreamReader] = None
        self._backoff = 1.0
        self._running = False

    @property
    def metrics(self) -> TsdbMetrics:
        return self._metrics

    async def start(self) -> None:
        if not self._running:
            self._running = True
            self._task = asyncio.create_task(self._flush_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await self._close_connection()

    async def enqueue(self, event: MarketEvent) -> None:
        if isinstance(event, L1Event):
            self._l1_cache[event.symbol] = event
        elif isinstance(event, Bar1sEvent):
            try:
                self._bars_queue.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    self._bars_queue.get_nowait()
                    self._bars_queue.put_nowait(event)
                    self._metrics.dropped_rows += 1
                except asyncio.QueueEmpty:
                    self._metrics.dropped_rows += 1

    async def _flush_loop(self) -> None:
        while self._running:
            await asyncio.sleep(self._flush_interval)
            await self._flush()

    async def _flush(self) -> None:
        async with self._lock:
            lines = self._build_batch()
            if not lines:
                return
            try:
                await self._write_batch(lines)
                self._metrics.written_rows += len(lines)
                self._metrics.last_flush_ts = time.time()
            except Exception as exc:
                logging.warning("QuestDB ILP flush failed: %s", exc)
                self._metrics.errors += 1
                await self._close_connection()

    def _build_batch(self) -> List[str]:
        lines: List[str] = []
        for symbol, event in list(self._l1_cache.items()):
            lines.append(self._format_l1(event))
            self._l1_cache.pop(symbol, None)
            if len(lines) >= self._batch_rows:
                return lines
        while len(lines) < self._batch_rows and not self._bars_queue.empty():
            try:
                bar = self._bars_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            lines.append(self._format_bar(bar))
        return lines

    def queue_depth(self) -> int:
        return self._bars_queue.qsize()

    @staticmethod
    def _format_l1(event: L1Event) -> str:
        ts = event.ts_ns
        return (
            f"market_l1,symbol={event.symbol} "
            f"bid={event.best_bid},ask={event.best_ask},bid_sz={event.bid_size},ask_sz={event.ask_size} {ts}"
        )

    @staticmethod
    def _format_bar(event: Bar1sEvent) -> str:
        ts = event.ts_ns
        return (
            f"bars_1s,symbol={event.symbol} "
            f"open={event.open},high={event.high},low={event.low},close={event.close},"
            f"volume={event.volume},trades={int(event.trades)}i {ts}"
        )

    async def _write_batch(self, lines: Iterable[str]) -> None:
        await self._ensure_connection()
        payload = "\n".join(lines) + "\n"
        self._writer.write(payload.encode("utf-8"))
        await self._writer.drain()

    async def _ensure_connection(self) -> None:
        if self._writer and not self._writer.is_closing():
            return
        await self._close_connection()
        while self._running:
            try:
                reader, writer = await asyncio.open_connection(self._config.host, self._config.ilp_port)
                self._reader = reader
                self._writer = writer
                self._backoff = 1.0
                logging.info("QuestDB ILP connected to %s:%s", self._config.host, self._config.ilp_port)
                return
            except (OSError, asyncio.TimeoutError) as exc:
                logging.warning("QuestDB ILP connect failed: %s", exc)
                await asyncio.sleep(self._backoff)
                self._backoff = min(self._backoff * 2, 10.0)

    async def _close_connection(self) -> None:
        if self._writer:
            self._writer.close()
            with contextlib.suppress(Exception):
                await self._writer.wait_closed()
        self._writer = None
        self._reader = None
