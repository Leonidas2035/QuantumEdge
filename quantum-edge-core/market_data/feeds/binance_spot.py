"""Stub implementation for a Binance spot feed."""

from __future__ import annotations

import asyncio
import logging
import time

from market_data.bus.event_bus import EventBus
from market_data.config import HubConfig
from market_data.feeds.base import BaseFeed
from market_data.models import HeartbeatEvent, Priority


class BinanceSpotFeed(BaseFeed):
    """Binance spot feed placeholder."""

    def __init__(self, config: HubConfig, bus: EventBus) -> None:
        super().__init__(config, bus)
        self.symbols = config.symbols

    async def _run(self) -> None:
        attempt = 0
        while not self._stop_event.is_set():
            attempt += 1
            logging.debug("BinanceSpotFeed heartbeats attempt=%s", attempt)
            event = HeartbeatEvent(
                ts_ns=time.time_ns(),
                symbol=self.symbols[0],
                event_type="heartbeat",
                seq=self.bus.assign_sequence(self.symbols[0], "heartbeat"),
                priority=Priority.L2,
                peer="binance-spot",
                extra={"status": "stub"},
            )
            await self.bus.publish(event)
            await asyncio.sleep(1)
