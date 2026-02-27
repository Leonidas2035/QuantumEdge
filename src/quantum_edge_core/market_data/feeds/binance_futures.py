"""Binance Futures WebSocket Feed — Kline (1m) mode.

Subscribes to Mainnet ``@kline_1m`` stream and emits ``KlineEvent``
objects into the EventBus.  Each WS push produces one event so the
downstream pipeline (dispatcher → ZMQ → bot) receives live candle
updates every ~2 s.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import websockets
from typing import Any, Dict

from quantum_edge_core.market_data.bus.event_bus import EventBus
from quantum_edge_core.market_data.config import HubConfig
from quantum_edge_core.market_data.feeds.base import BaseFeed
from quantum_edge_core.events import KlineEvent


class BinanceFuturesFeed(BaseFeed):
    """
    Connects to Binance Futures Mainnet ``@kline_1m`` WebSocket stream
    and emits KlineEvent via the internal EventBus.

    Default URL uses Mainnet for reliable data; execution remains on
    Testnet via the separate execution gateway.
    """

    DEFAULT_WS_URL = "wss://fstream.binance.com/ws"

    def __init__(self, config: HubConfig, bus: EventBus) -> None:
        super().__init__(config, bus)
        self.symbols = [s.lower() for s in config.symbols]
        self.ws_url = os.getenv("BINANCE_WS_URL", self.DEFAULT_WS_URL)
        self.logger = logging.getLogger("BinanceFuturesFeed")

    async def _run(self) -> None:
        """Main run loop with exponential back-off reconnection."""
        retry_delay = 1.0
        while not self._stop_event.is_set():
            try:
                await self._connect_and_listen()
                retry_delay = 1.0  # Reset on success
            except Exception as exc:
                if self._stop_event.is_set():
                    break
                self.logger.error(
                    "Connection failed: %s. Retrying in %.1fs", exc, retry_delay
                )
                await self._sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60.0)

    async def _connect_and_listen(self) -> None:
        # Subscribe to 1-minute kline for each symbol
        streams = [f"{s}@kline_1m" for s in self.symbols]
        stream_path = "/".join(streams)
        url = f"{self.ws_url}/{stream_path}"

        self.logger.info("Connecting to Binance Futures (kline_1m): %s", url)

        async with websockets.connect(url) as ws:
            self.logger.info("Connected to %s", url)

            while not self._stop_event.is_set():
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=120.0)
                    # HARD LOGGING — raw data visibility
                    self.logger.info("RAW WS DATA RECEIVED: %s", str(msg)[:150])
                    await self._handle_message(msg)
                except asyncio.TimeoutError:
                    self.logger.warning(
                        "No kline data received for 120s, reconnecting..."
                    )
                    return

    async def _handle_message(self, msg: str | bytes) -> None:
        """Parse Binance kline payload and emit KlineEvent."""
        try:
            data: Dict[str, Any] = json.loads(msg)
            event_type = data.get("e")

            if event_type == "kline":
                # Binance kline payload structure:
                # {
                #   "e": "kline",
                #   "E": 123456789,      // Event time (ms)
                #   "s": "BTCUSDT",      // Symbol
                #   "k": {
                #     "t": 123400000,    // Kline start time (ms)
                #     "T": 123459999,    // Kline close time (ms)
                #     "s": "BTCUSDT",
                #     "i": "1m",         // Interval
                #     "f": 100,          // First trade ID
                #     "L": 200,          // Last trade ID
                #     "o": "0.0010",     // Open price
                #     "c": "0.0020",     // Close price
                #     "h": "0.0025",     // High price
                #     "l": "0.0015",     // Low price
                #     "v": "1000",       // Base asset volume
                #     "n": 100,          // Number of trades
                #     "x": false,        // Is this kline closed?
                #   }
                # }
                k = data["k"]
                symbol = data["s"]
                close_price = float(k["c"])
                volume = float(k["v"])
                kline_open_time_ms = int(k["t"])

                event = KlineEvent(
                    priority="L0",
                    event_type="kline",
                    seq=self.bus.assign_sequence(symbol, "kline"),
                    symbol=symbol,
                    interval=k["i"],
                    open=float(k["o"]),
                    high=float(k["h"]),
                    low=float(k["l"]),
                    close=close_price,
                    volume=volume,
                    trades=int(k["n"]),
                    is_closed=bool(k.get("x", False)),
                    # Bot-compatible aliases
                    price=close_price,
                    quantity=volume,
                    timestamp=kline_open_time_ms / 1000.0,
                )
                await self.bus.publish(event)

        except Exception as exc:
            self.logger.error("Error processing kline message: %s", exc)
