"""Entry point for the MarketDataHub service."""

from __future__ import annotations

import asyncio
import logging
import signal
import time
from contextlib import suppress

from MarketDataHub.bus.event_bus import EventBus
from MarketDataHub.config import HubConfig
from MarketDataHub.feeds.binance_futures import BinanceFuturesFeed
from MarketDataHub.feeds.binance_spot import BinanceSpotFeed
from MarketDataHub.ipc.publisher import ZmqPublisher
from MarketDataHub.ipc.snapshot_server import SnapshotCache, SnapshotServer
from MarketDataHub.models import HeartbeatEvent, Priority
from MarketDataHub.tsdb.quest_writer import QuestWriter


class MarketDataHubService:
    """Service orchestrating the data-plane components."""

    def __init__(self, config: HubConfig | None = None) -> None:
        self.config = config or HubConfig.load()
        self.bus = EventBus(l0_hwm=self.config.l0_hwm, l1_hwm=self.config.l1_hwm)
        self.publisher = ZmqPublisher(self.config)
        self.writer = QuestWriter(self.config)
        self.snapshot_cache = SnapshotCache(trade_tail=self.config.snapshot.trade_tail)
        self.snapshot_server = SnapshotServer(self.config, self.snapshot_cache)
        self.feeds = [
            BinanceSpotFeed(self.config, self.bus),
            BinanceFuturesFeed(self.config, self.bus),
        ]
        self._tasks: list[asyncio.Task] = []
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        logging.basicConfig(level=self.config.log_level)
        self.writer.start()
        self.snapshot_server.start()
        for feed in self.feeds:
            await feed.start()
        self._tasks.extend(
            [
                asyncio.create_task(self._dispatcher_loop()),
                asyncio.create_task(self._heartbeat_loop()),
            ]
        )
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(self.stop()))
            except NotImplementedError:
                logging.debug("Signal handlers not supported for %s", sig)
        await self._stop_event.wait()

    async def stop(self) -> None:
        self._stop_event.set()
        for feed in self.feeds:
            await feed.stop()
        self.writer.stop()
        self.publisher.close()
        self.snapshot_server.stop()
        for task in self._tasks:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def _dispatcher_loop(self) -> None:
        while not self._stop_event.is_set():
            event = await self.bus.get_event()
            self.snapshot_cache.update(event)
            self.publisher.publish(event)
            await self.writer.enqueue(event)

    async def _heartbeat_loop(self) -> None:
        symbol = self.config.symbols[0]
        while not self._stop_event.is_set():
            event = HeartbeatEvent(
                ts_ns=time.time_ns(),
                symbol=symbol,
                event_type="heartbeat",
                seq=self.bus.assign_sequence(symbol, "heartbeat"),
                priority=Priority.L2,
                peer="heart",
                extra={"status": "ok"},
            )
            await self.bus.publish(event)
            await asyncio.sleep(5)


async def run() -> None:
    service = MarketDataHubService()
    await service.start()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
