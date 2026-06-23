"""BingX Perpetual Swap WebSocket Feed.

Subscribes to BingX streams per symbol:
    - trade
    - depth20@100ms
    - kline_1m

Emits MarketTrade, OrderBookUpdate, and KlineEvent objects into the EventBus.
"""

from __future__ import annotations

import asyncio
import gzip
import io
import json
import logging
import os
import time
import aiohttp
from typing import Any, Dict, List

from quantum_edge_core.market_data.bus.event_bus import EventBus
from quantum_edge_core.market_data.config import HubConfig
from quantum_edge_core.market_data.feeds.base import BaseFeed
from quantum_edge_core.market_data.lob_manager import OrderBookManager
from quantum_edge_core.events import (
    KlineEvent,
    OrderBookUpdate,
    MarketTrade,
    WhaleWall,
)


def to_bingx_symbol(symbol: str) -> str:
    """Convert BTCUSDT to BTC-USDT."""
    if "-" in symbol:
        return symbol.upper()
    if symbol.upper().endswith("USDT"):
        base = symbol.upper()[:-4]
        return f"{base}-USDT"
    return symbol.upper()


def from_bingx_symbol(symbol: str) -> str:
    """Convert BTC-USDT to BTCUSDT."""
    return symbol.replace("-", "").upper()


class BingXLiveFeed(BaseFeed):
    """
    Connects to BingX Perpetual Swap WebSocket streams and emits typed
    events via the internal EventBus.
    """

    DEFAULT_WS_URL = "wss://open-api-swap.bingx.com/swap-market"

    def __init__(self, config: HubConfig, bus: EventBus) -> None:
        super().__init__(config, bus)
        self.symbols = [s.upper() for s in config.symbols]
        self.ws_url = os.getenv("BINGX_WS_URL", self.DEFAULT_WS_URL)
        self.logger = logging.getLogger("BingXLiveFeed")
        self._lob: Dict[str, OrderBookManager] = {
            s: OrderBookManager(symbol=s) for s in self.symbols
        }

    async def _run(self) -> None:
        """Main run loop with reconnection logic."""
        retry_delay = 1.0
        while not self._stop_event.is_set():
            try:
                await self._connect_and_listen()
                retry_delay = 1.0  # Reset delay on clean disconnect
            except Exception as e:
                if self._stop_event.is_set():
                    break
                self.logger.error(
                    "BingX connection failed: %s. Retrying in %.1fs",
                    e,
                    retry_delay,
                )
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60.0)

    async def _connect_and_listen(self) -> None:
        self.logger.info("Connecting to BingX Swap WS: %s", self.ws_url)
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(self.ws_url) as ws:
                self.logger.info("Connected to BingX WS")

                # Subscribe to streams for each symbol
                for s in self.symbols:
                    bx_sym = to_bingx_symbol(s)

                    # 1. Subscribe to trades
                    await ws.send_json({
                        "id": f"trade_{s}",
                        "reqType": "sub",
                        "dataType": f"{bx_sym}@trade"
                    })

                    # 2. Subscribe to depth20@100ms
                    await ws.send_json({
                        "id": f"depth_{s}",
                        "reqType": "sub",
                        "dataType": f"{bx_sym}@depth20@100ms"
                    })

                    # 3. Subscribe to kline_1m
                    await ws.send_json({
                        "id": f"kline_{s}",
                        "reqType": "sub",
                        "dataType": f"{bx_sym}@kline_1m"
                    })

                # Listen to incoming messages
                while not self._stop_event.is_set():
                    try:
                        msg = await asyncio.wait_for(ws.receive(), timeout=120.0)
                        if msg.type == aiohttp.WSMsgType.BINARY:
                            await self._handle_binary_message(msg.data, ws)
                        elif msg.type == aiohttp.WSMsgType.CLOSED:
                            self.logger.warning("BingX WS closed by server")
                            return
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            self.logger.error("BingX WS connection error")
                            return
                    except asyncio.TimeoutError:
                        self.logger.warning("No data from BingX WS for 120s, reconnecting...")
                        return

    async def _handle_binary_message(self, data_bytes: bytes, ws) -> None:
        # Decompress GZIP
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(data_bytes)) as f:
                decompressed = f.read()
            data_str = decompressed.decode("utf-8")
        except Exception as e:
            self.logger.error("Failed to decompress BingX WS message: %s", e)
            return

        if "Ping" in data_str:
            try:
                await ws.send_str("Pong")
            except Exception as e:
                self.logger.error("Failed to send Pong: %s", e)
            return

        try:
            payload = json.loads(data_str)
        except Exception as e:
            self.logger.error("Failed to parse BingX JSON: %s", e)
            return

        # Check for errors in subscription responses
        if "code" in payload and payload["code"] != 0:
            self.logger.error("BingX subscription error: %s", payload.get("msg"))
            return

        data_type = payload.get("dataType", "")
        if not data_type:
            return

        # Parse channel and symbol
        # e.g., "BTC-USDT@trade" -> "BTC-USDT", "trade"
        parts = data_type.split("@")
        if len(parts) < 2:
            return
        bx_sym, stream_type = parts[0], parts[1]
        symbol = from_bingx_symbol(bx_sym)

        # Get payload data
        data_list = payload.get("data")
        if not data_list:
            return

        if stream_type == "trade":
            await self._handle_trades(symbol, data_list)
        elif stream_type.startswith("depth"):
            await self._handle_depth(symbol, data_list, payload.get("ts", 0))
        elif stream_type.startswith("kline"):
            await self._handle_klines(symbol, data_list)

    async def _handle_trades(self, symbol: str, trades: list) -> None:
        for t in trades:
            price = float(t["p"])
            qty = float(t["q"])
            is_buyer_maker = bool(t.get("m", False))
            timestamp = float(t.get("T", time.time() * 1000)) / 1000.0

            event = MarketTrade(
                priority="L0",
                event_type="trade",
                seq=self.bus.assign_sequence(symbol, "trade"),
                symbol=symbol,
                price=price,
                quantity=qty,
                side="buy" if is_buyer_maker else "sell",
                timestamp=timestamp,
            )
            await self.bus.publish(event)

    async def _handle_depth(self, symbol: str, depth_data: dict, ts_ms: int) -> None:
        try:
            bids = depth_data.get("bids", [])
            asks = depth_data.get("asks", [])

            if not bids or not asks:
                raise ValueError("Empty orderbook sides received")

            lob = self._lob.get(symbol)
            if lob is None:
                lob = OrderBookManager(symbol=symbol)
                self._lob[symbol] = lob

            # Handle different BingX formats (dict vs list)
            best_bid = float(bids[0]["price"] if isinstance(bids[0], dict) else bids[0][0])
            best_ask = float(asks[0]["price"] if isinstance(asks[0], dict) else asks[0][0])
            
            mid_price = (best_bid + best_ask) / 2.0
            spread = best_ask - best_bid

            # Since it's a snapshot, we replace the bids and asks in the LOB manager
            lob._bids = {float(p["price"] if isinstance(p, dict) else p[0]): float(p["qty"] if isinstance(p, dict) else p[1]) for p in bids}
            lob._asks = {float(p["price"] if isinstance(p, dict) else p[0]): float(p["qty"] if isinstance(p, dict) else p[1]) for p in asks}
            lob._initialized = True

            # Compute mid/spread/whale walls
            snap_data = lob.get_snapshot(depth=20, wall_threshold=20.0)

            walls = [
                WhaleWall(
                    priority="L0",
                    event_type="whale_wall",
                    seq=0,
                    side=w["side"],
                    price=w["price"],
                    quantity=w["quantity"],
                )
                for w in snap_data["whale_walls"]
            ]

            event = OrderBookUpdate(
                priority="L0",
                event_type="depth",
                seq=self.bus.assign_sequence(symbol, "depth"),
                symbol=symbol,
                bids=snap_data["bids"],
                asks=snap_data["asks"],
                mid=mid_price,
                mid_price=mid_price,
                spread=spread,
                whale_walls=walls,
                timestamp=float(ts_ms or time.time() * 1000) / 1000.0,
            )
            await self.bus.publish(event)
        except Exception as e:
            self.logger.error(f"BingX L2 Parser Error: {e} - Payload: {depth_data}")

    async def _handle_klines(self, symbol: str, kline_list: list) -> None:
        for k in kline_list:
            close_price = float(k["c"])
            volume = float(k["v"])
            open_time_ms = int(k["T"])

            event = KlineEvent(
                priority="L0",
                event_type="kline",
                seq=self.bus.assign_sequence(symbol, "kline"),
                symbol=symbol,
                interval="1m",
                open=float(k["o"]),
                high=float(k["h"]),
                low=float(k["l"]),
                close=close_price,
                volume=volume,
                trades=0,
                is_closed=False,
                price=close_price,
                quantity=volume,
                timestamp=open_time_ms / 1000.0,
            )
            await self.bus.publish(event)
