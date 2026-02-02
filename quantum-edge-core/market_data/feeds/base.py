"""Base feed interfaces for MarketDataHub."""

from __future__ import annotations

import abc
import asyncio
import logging

from market_data.bus.event_bus import EventBus
from market_data.config import HubConfig


class BaseFeed(abc.ABC):
    """Minimal feed contract."""

    def __init__(self, config: HubConfig, bus: EventBus) -> None:
        self.config = config
        self.bus = bus
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            await self._task

    @abc.abstractmethod
    async def _run(self) -> None:
        raise NotImplementedError

    def _sleep(self, seconds: float) -> asyncio.Task:
        return asyncio.create_task(asyncio.sleep(seconds))

    def _log_backoff(self, attempt: int) -> None:
        logging.info("Feed backoff attempt=%s", attempt)
