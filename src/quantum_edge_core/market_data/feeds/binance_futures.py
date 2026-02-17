"""Binance Futures WebSocket Feed."""

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
from quantum_edge_core.market_data.models import Priority, TradeEvent


class BinanceFuturesFeed(BaseFeed):
    """
    Connects to Binance Futures WebSocket streams and emits TradeEvent.
    Supports both Production and Testnet via BINANCE_WS_URL env var.
    """

    DEFAULT_WS_URL = "wss://fstream.binance.com/ws"

    def __init__(self, config: HubConfig, bus: EventBus) -> None:
        super().__init__(config, bus)
        self.symbols = [s.lower() for s in config.symbols]
        self.ws_url = os.getenv("BINANCE_WS_URL", self.DEFAULT_WS_URL)
        self.logger = logging.getLogger("BinanceFuturesFeed")

    async def _run(self) -> None:
        """Main run loop with reconnection logic."""
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
        # Construct stream URL: /ws/<stream1>/<stream2>...
        # Using 'trade' stream (real-time trades)
        streams = [f"{s}@trade" for s in self.symbols]
        stream_path = "/".join(streams)
        url = f"{self.ws_url}/{stream_path}"

        self.logger.info("Connecting to Binance Futures: %s", url)

        async with websockets.connect(url) as ws:
            self.logger.info("Connected to %s", url)

            while not self._stop_event.is_set():
                try:
                    # Binance sends ping frames automatically, websockets handles pong
                    msg = await asyncio.wait_for(ws.recv(), timeout=60.0)
                    await self._handle_message(msg)
                except asyncio.TimeoutError:
                    self.logger.warning("No data received for 60s, reconnecting...")
                    return

    async def _handle_message(self, msg: str | bytes) -> None:
        try:
            data: Dict[str, Any] = json.loads(msg)
            event_type = data.get("e")

            if event_type == "trade":
                # {
                #   "e": "trade",     // Event type
                #   "E": 123456789,   // Event time
                #   "s": "BNBUSDT",   // Symbol
                #   "t": 12345,       // Trade ID
                #   "p": "0.001",     // Price
                #   "q": "100",       // Quantity
                #   "b": 88,          // Buyer order ID
                #   "a": 50,          // Seller order ID
                #   "T": 123456785,   // Trade time
                #   "m": true,        // Is the buyer the market maker?
                #   "X": "MARKET"     // Type of trade (not always present)
                # }

                # Logic: If m=True, Maker is Buyer -> Taker is Seller.
                #        If m=False, Maker is Seller -> Taker is Buyer.
                taker_side = "sell" if data.get("m") else "buy"

                event = TradeEvent(
                    ts_ns=int(data["T"]) * 1_000_000,  # ms to ns
                    symbol=data["s"],
                    event_type="trade",
                    seq=self.bus.assign_sequence(data["s"], "trade"),
                    priority=Priority.L0,
                    price=float(data["p"]),
                    size=float(data["q"]),
                    taker_side=taker_side,
                )
                await self.bus.publish(event)

        except Exception as exc:
            self.logger.error("Error processing message: %s", exc)
