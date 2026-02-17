"""
src/quantum_edge_core/market_data/feeds/binance_feed.py

Async Binance WebSocket Feed with robust reconnection logic.
"""

import asyncio
import json
import websockets

from quantum_edge_core.core.service import BaseService
from quantum_edge_core.events import MarketTrade


class BinanceFeed(BaseService):
    """
    Connects to Binance WebSocket streams and emits MarketTrade events.
    """

    WS_URL = "wss://testnet.binance.vision/ws"

    def __init__(self, symbols: list[str]):
        super().__init__("BinanceFeed")
        self.symbols = [s.lower() for s in symbols]
        self._stop_event = asyncio.Event()

    async def run(self):
        """
        Main run loop with exponential backoff.
        """
        retry_delay = 1.0

        while not self._stop_event.is_set():
            try:
                await self._connect_and_listen()
                retry_delay = (
                    1.0  # Reset on successful connection (if it lasted a while)
                )
            except Exception as e:
                if self._stop_event.is_set():
                    break

                self.logger.error(
                    "Connection failed", error=str(e), retry_in=retry_delay
                )
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 30.0)  # Cap at 30s

    async def _connect_and_listen(self):
        streams = [f"{s}@trade" for s in self.symbols]
        stream_path = "/".join(streams)
        url = f"{self.WS_URL}/{stream_path}"

        self.logger.info("Connecting to Binance", url=url)

        async with websockets.connect(url) as ws:
            self.logger.info("Connected to Binance")

            while not self._stop_event.is_set():
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=60.0)
                    await self._handle_message(msg)
                except asyncio.TimeoutError:
                    # Ping or keepalive logic could go here
                    self.logger.warning("No data received for 60s, reconnecting...")
                    return

    async def _handle_message(self, msg: str):
        """
        Parse JSON and emit event.
        """
        try:
            data = json.loads(msg)
            # Binance Trade Payload:
            # {"e": "trade", "E": 123456789, "s": "BNBBTC", "p": "0.001", "q": "100", ...}

            if data.get("e") == "trade":
                trade = MarketTrade(
                    symbol=data["s"],
                    price=float(data["p"]),
                    quantity=float(data["q"]),
                    side=(
                        "buy" if data["m"] else "sell"
                    ),  # m=True means maker was buyer -> side=sell
                    timestamp=data["E"] / 1000.0,
                )
                # In a real app, we'd emit this to a bus. For now, just log it debug.
                self.logger.debug("Trade received", trade=trade)

        except Exception as e:
            self.logger.error("Failed to parse message", error=str(e), msg=msg[:100])

    async def cleanup(self):
        self._stop_event.set()
