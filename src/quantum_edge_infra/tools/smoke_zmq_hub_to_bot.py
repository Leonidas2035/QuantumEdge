"""Smoke tool: publishes MarketDataHub events and shows bot SUB + snapshot recovery."""

import asyncio
import time
from typing import Iterable

from market_data.config import HubConfig
from market_data.ipc.publisher import ZmqPublisher
from market_data.ipc.snapshot_server import SnapshotCache, SnapshotServer
from market_data.models import L1Event, Priority

from bot.market_data.hub_source import HubMarketDataSource


async def _publish_events(publisher: ZmqPublisher, cache: SnapshotCache, symbols: Iterable[str]) -> None:
    symbol = next(iter(symbols))
    event = L1Event(
        ts_ns=time.time_ns(),
        symbol=symbol,
        event_type="l1",
        seq=1,
        priority=Priority.L1,
        best_bid=1.0,
        best_ask=1.5,
        bid_size=1.0,
        ask_size=1.0,
    )
    cache.update(event)
    publisher.publish(event)
    await asyncio.sleep(0.2)
    gap_event = L1Event(
        ts_ns=time.time_ns(),
        symbol=symbol,
        event_type="l1",
        seq=3,
        priority=Priority.L1,
        best_bid=1.1,
        best_ask=1.6,
        bid_size=1.2,
        ask_size=1.2,
    )
    cache.update(gap_event)
    publisher.publish(gap_event)


async def main() -> None:
    cfg = HubConfig()
    cache = SnapshotCache(trade_tail=cfg.snapshot.trade_tail)
    server = SnapshotServer(cfg, cache)
    server.start()
    publisher = ZmqPublisher(cfg)
    symbols = cfg.symbols
    hub_cfg = {
        "hub": {
            "pub_endpoint": cfg.zmq.endpoint,
            "snapshot_endpoint": cfg.snapshot.endpoint,
            "topics": [f"{symbols[0]}:l1"],
        }
    }
    source = HubMarketDataSource(symbols, hub_cfg)

    try:
        await source.start()
        reader_task = asyncio.create_task(_drain_events(source))
        await _publish_events(publisher, cache, symbols)
        await reader_task
    finally:
        await source.stop()
        server.stop()
        publisher.close()


async def _drain_events(source: HubMarketDataSource) -> None:
    count = 0
    async for event in source.stream():
        print("bot event:", event)
        count += 1
        if count >= 3:
            break


if __name__ == "__main__":
    asyncio.run(main())
