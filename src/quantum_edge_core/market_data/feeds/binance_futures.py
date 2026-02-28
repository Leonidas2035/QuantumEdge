"""Binance Futures WebSocket Feed — Kline (1m) + L2 Depth mode.

Subscribes to Mainnet ``@kline_1m`` and ``@depth5@100ms`` streams
and emits ``KlineEvent`` / ``OrderBookUpdate`` objects into the
EventBus.  Kline pushes arrive every ~2 s; depth pushes arrive
every 100 ms for low-latency order book data.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import websockets
from typing import Any, Dict, List

from quantum_edge_core.market_data.bus.event_bus import EventBus
from quantum_edge_core.market_data.config import HubConfig
from quantum_edge_core.market_data.feeds.base import BaseFeed
from quantum_edge_core.events import KlineEvent, OrderBookUpdate


class BinanceFuturesFeed(BaseFeed):
    """
    Connects to Binance Futures Mainnet WebSocket streams
    (``@kline_1m`` + ``@depth5@100ms``) and emits KlineEvent /
    OrderBookUpdate via the internal EventBus.

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
        # Combined streams: kline_1m + depth5@100ms per symbol
        streams: List[str] = []
        for s in self.symbols:
            streams.append(f"{s}@kline_1m")
            streams.append(f"{s}@depth5@100ms")

        stream_path = "/".join(streams)
        url = f"{self.ws_url}/{stream_path}"

        self.logger.info("Connecting to Binance Futures (kline+depth): %s", url)

        async with websockets.connect(url) as ws:
            self.logger.info("Connected to %s", url)

            while not self._stop_event.is_set():
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=120.0)
                    # HARD LOGGING — raw data visibility (truncated for depth)
                    self.logger.info("RAW WS DATA RECEIVED: %s", str(msg)[:150])
                    await self._handle_message(msg)
                except asyncio.TimeoutError:
                    self.logger.warning(
                        "No data received for 120s, reconnecting..."
                    )
                    return

    async def _handle_message(self, msg: str | bytes) -> None:
        """Parse Binance kline / depth payload and emit events."""
        try:
            data: Dict[str, Any] = json.loads(msg)
            event_type = data.get("e")

            if event_type == "kline":
                await self._handle_kline(data)
            elif event_type == "depthUpdate":
                await self._handle_depth(data)

        except Exception as exc:
            self.logger.error("Error processing WS message: %s", exc, exc_info=True)

    async def _handle_kline(self, data: Dict[str, Any]) -> None:
        """Emit KlineEvent from Binance kline payload."""
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

    async def _handle_depth(self, data: Dict[str, Any]) -> None:
        """Emit OrderBookUpdate from Binance partial depth payload.

        Binance ``@depth5@100ms`` payload:
        {
            "e": "depthUpdate",
            "E": 123456789,    // Event time (ms)
            "T": 123456788,    // Transaction time (ms)
            "s": "BTCUSDT",
            "U": 157,          // First update ID
            "u": 160,          // Final update ID
            "pu": 156,         // Previous final update ID
            "b": [             // Bids: [[price, qty], ...]
                ["65500.00", "1.234"],
                ...
            ],
            "a": [             // Asks: [[price, qty], ...]
                ["65501.00", "0.567"],
                ...
            ]
        }
        """
        symbol = data.get("s", "BTCUSDT")
        event_time_ms = data.get("E", 0)

        # Convert string pairs to float pairs
        bids = [[float(p), float(q)] for p, q in data.get("b", [])]
        asks = [[float(p), float(q)] for p, q in data.get("a", [])]

        if not bids and not asks:
            return

        event = OrderBookUpdate(
            priority="L0",
            event_type="depth",
            seq=self.bus.assign_sequence(symbol, "depth"),
            symbol=symbol,
            bids=bids,
            asks=asks,
            timestamp=event_time_ms / 1000.0,
        )
        await self.bus.publish(event)
