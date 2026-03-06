"""Binance Futures WebSocket Feed — Full Market Data Suite.

Subscribes to Mainnet streams per symbol:
    - ``@kline_1m``      — 1-minute candles
    - ``@depth@100ms``   — L2 depth deltas (incremental LOB)
    - ``@aggTrade``      — Aggregated trades
    - ``@forceOrder``    — Liquidation events

Emits ``KlineEvent``, ``OrderBookUpdate``, ``MarketTrade``, and
``LiquidationEvent`` objects into the EventBus.
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
from quantum_edge_core.market_data.lob_manager import OrderBookManager
from quantum_edge_core.events import (
    KlineEvent,
    OrderBookUpdate,
    MarketTrade,
    LiquidationEvent,
    WhaleWall,
)


class BinanceFuturesFeed(BaseFeed):
    """
    Connects to Binance Futures Mainnet WebSocket streams
    (kline + depth + aggTrade + forceOrder) and emits typed
    events via the internal EventBus.

    Default URL uses Mainnet for reliable data; execution remains on
    Testnet via the separate execution gateway.
    """

    DEFAULT_WS_URL = "wss://fstream.binance.com/ws"

    def __init__(self, config: HubConfig, bus: EventBus) -> None:
        super().__init__(config, bus)
        self.symbols = [s.lower() for s in config.symbols]
        self.ws_url = os.getenv("BINANCE_WS_URL", self.DEFAULT_WS_URL)
        self.logger = logging.getLogger("BinanceFuturesFeed")

        # Per-symbol local order book managers
        self._lob: Dict[str, OrderBookManager] = {
            s.upper(): OrderBookManager(symbol=s.upper()) for s in self.symbols
        }

    # Binance IP ban / rate-limit HTTP codes
    _BAN_CODES: frozenset[int] = frozenset({418, 451, 403, 429})
    _BAN_BACKOFF_S: float = 300.0  # 5 min cool-down on IP ban

    async def _run(self) -> None:
        """Main run loop with exponential back-off reconnection.

        Detects Binance IP bans (HTTP 418/451/429) and applies extended
        backoff to prevent useless rapid reconnect loops.
        """
        retry_delay = 1.0
        while not self._stop_event.is_set():
            try:
                await self._connect_and_listen()
                retry_delay = 1.0  # Reset on clean disconnect
            except websockets.exceptions.InvalidStatusCode as exc:
                if self._stop_event.is_set():
                    break
                if exc.status_code in self._BAN_CODES:
                    self.logger.critical(
                        "BINANCE IP BAN DETECTED (HTTP %d). "
                        "Backing off for %.0fs. "
                        "Check WAF status / rotate IP / contact support.",
                        exc.status_code,
                        self._BAN_BACKOFF_S,
                    )
                    await self._sleep(self._BAN_BACKOFF_S)
                else:
                    self.logger.error(
                        "WebSocket rejected (HTTP %d): %s. Retrying in %.1fs",
                        exc.status_code,
                        exc,
                        retry_delay,
                    )
                    await self._sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, 60.0)
            except Exception as exc:
                if self._stop_event.is_set():
                    break
                self.logger.error(
                    "Connection failed: %s. Retrying in %.1fs", exc, retry_delay
                )
                await self._sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60.0)

    async def _connect_and_listen(self) -> None:
        # Full stream suite per symbol
        streams: List[str] = []
        for s in self.symbols:
            streams.append(f"{s}@kline_1m")
            streams.append(f"{s}@depth@100ms")
            streams.append(f"{s}@aggTrade")
            streams.append(f"{s}@forceOrder")

        stream_path = "/".join(streams)
        url = f"{self.ws_url}/{stream_path}"

        self.logger.info("Connecting to Binance Futures (full suite): %s", url)

        async with websockets.connect(url) as ws:
            self.logger.info("Connected to %s", url)

            while not self._stop_event.is_set():
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=120.0)
                    self.logger.debug("RAW WS: %s", str(msg)[:150])
                    await self._handle_message(msg)
                except asyncio.TimeoutError:
                    self.logger.warning("No data received for 120s, reconnecting...")
                    return

    async def _handle_message(self, msg: str | bytes) -> None:
        """Parse Binance payload and dispatch to typed handler."""
        try:
            data: Dict[str, Any] = json.loads(msg)
            event_type = data.get("e")

            if event_type == "kline":
                await self._handle_kline(data)
            elif event_type == "depthUpdate":
                await self._handle_depth(data)
            elif event_type == "aggTrade":
                await self._handle_agg_trade(data)
            elif event_type == "forceOrder":
                await self._handle_liquidation(data)

        except Exception as exc:
            self.logger.error("Error processing WS message: %s", exc, exc_info=True)

    # ── Kline Handler ─────────────────────────────────────────

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
            price=close_price,
            quantity=volume,
            timestamp=kline_open_time_ms / 1000.0,
        )
        await self.bus.publish(event)

    # ── Depth Handler (LOB Integration) ───────────────────────

    async def _handle_depth(self, data: Dict[str, Any]) -> None:
        """Process depth delta, update local LOB, emit enriched OrderBookUpdate.

        Binance ``@depth@100ms`` payload:
        {
            "e": "depthUpdate",
            "E": 123456789,     // Event time (ms)
            "s": "BTCUSDT",
            "U": 157,           // First update ID
            "u": 160,           // Final update ID
            "b": [["65500.00", "1.234"], ...],  // Bid deltas
            "a": [["65501.00", "0.567"], ...]   // Ask deltas
        }
        """
        symbol = data.get("s", "BTCUSDT")
        event_time_ms = data.get("E", 0)
        final_update_id = data.get("u", 0)

        bids_delta = data.get("b", [])
        asks_delta = data.get("a", [])

        if not bids_delta and not asks_delta:
            return

        # 1. Update local order book
        lob = self._lob.get(symbol)
        if lob is None:
            lob = OrderBookManager(symbol=symbol)
            self._lob[symbol] = lob

        lob.apply_delta(bids_delta, asks_delta, final_update_id)

        # 2. Get snapshot with whale walls (top 20 levels)
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

        # 3. Emit enriched OrderBookUpdate
        event = OrderBookUpdate(
            priority="L0",
            event_type="depth",
            seq=self.bus.assign_sequence(symbol, "depth"),
            symbol=symbol,
            bids=snap_data["bids"],
            asks=snap_data["asks"],
            timestamp=event_time_ms / 1000.0,
            whale_walls=walls,
        )
        await self.bus.publish(event)

    # ── Aggregated Trade Handler ──────────────────────────────

    async def _handle_agg_trade(self, data: Dict[str, Any]) -> None:
        """Emit MarketTrade from Binance aggTrade payload.

        Binance ``@aggTrade`` payload:
        {
            "e": "aggTrade",
            "E": 123456789,     // Event time (ms)
            "s": "BTCUSDT",
            "a": 5933014,       // Aggregate trade ID
            "p": "65500.00",    // Price
            "q": "0.500",       // Quantity
            "f": 100,           // First trade ID
            "l": 105,           // Last trade ID
            "T": 123456785,     // Trade time (ms)
            "m": true           // Is buyer maker?
        }
        """
        symbol = data.get("s", "BTCUSDT")
        price = float(data.get("p", 0.0))
        qty = float(data.get("q", 0.0))
        trade_time_ms = data.get("T", data.get("E", 0))
        is_buyer_maker = data.get("m", False)

        side = "sell" if is_buyer_maker else "buy"

        event = MarketTrade(
            priority="L0",
            event_type="trade",
            seq=self.bus.assign_sequence(symbol, "trade"),
            symbol=symbol,
            price=price,
            quantity=qty,
            side=side,
            timestamp=trade_time_ms / 1000.0,
        )
        await self.bus.publish(event)

    # ── Liquidation Handler ───────────────────────────────────

    async def _handle_liquidation(self, data: Dict[str, Any]) -> None:
        """Emit LiquidationEvent from Binance forceOrder payload.

        Binance ``@forceOrder`` payload:
        {
            "e": "forceOrder",
            "E": 123456789,
            "o": {
                "s": "BTCUSDT",
                "S": "SELL",        // Side
                "o": "LIMIT",       // Order type
                "f": "IOC",
                "q": "0.014",       // Original quantity
                "p": "65500.00",    // Price
                "ap": "65480.00",   // Average price
                "X": "FILLED",
                "l": "0.014",       // Last filled qty
                "z": "0.014",       // Cumulative filled qty
                "T": 123456789      // Trade time (ms)
            }
        }
        """
        order = data.get("o", {})
        symbol = order.get("s", "BTCUSDT")
        side = order.get("S", "SELL")
        price = float(order.get("p", 0.0))
        qty = float(order.get("q", 0.0))
        trade_time_ms = order.get("T", data.get("E", 0))

        usd_size = price * qty

        event = LiquidationEvent(
            priority="L0",
            event_type="liquidation",
            seq=self.bus.assign_sequence(symbol, "liquidation"),
            symbol=symbol,
            side=side,
            price=price,
            qty=qty,
            usd_size=usd_size,
            timestamp=trade_time_ms / 1000.0,
        )
        await self.bus.publish(event)
