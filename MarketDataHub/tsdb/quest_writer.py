"""QuestDB ILP writer stub for MarketDataHub."""

from __future__ import annotations

import asyncio
import logging
from typing import Iterable

from MarketDataHub.config import HubConfig
from MarketDataHub.models import MarketEvent


class QuestWriter:
    """Async micro-batch writer (warm path)."""

    def __init__(self, config: HubConfig) -> None:
        self._config = config
        self._queue: asyncio.Queue[MarketEvent] = asyncio.Queue()
        self._batch_rows = config.quest.batch_rows
        self._flush_interval = config.quest.flush_interval_ms / 1000.0
        self._task: asyncio.Task | None = None

    async def enqueue(self, event: MarketEvent) -> None:
        await self._queue.put(event)

    async def flush_loop(self) -> None:
        while True:
            await asyncio.sleep(self._flush_interval)
            batch = self._drain_batch()
            if batch:
                await self._flush_batch(batch)

    def _drain_batch(self) -> list[MarketEvent]:
        batch: list[MarketEvent] = []
        while not self._queue.empty() and len(batch) < self._batch_rows:
            try:
                batch.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return batch

    async def _flush_batch(self, batch: Iterable[MarketEvent]) -> None:
        logging.info("QuestWriter flushing %d events (stub)", sum(1 for _ in batch))
        # TODO: emit ILP lines via msgspec + aiohttp/curio; keep stub for Stage1.

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.flush_loop())

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
