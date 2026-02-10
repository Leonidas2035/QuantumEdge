"""
src/quantum_edge_core/market_data/feeds/liquidations.py

Ingests forceOrder (Liquidation) events from Binance Futures.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Set

import websockets

from quantum_edge_core.market_data.bus.event_bus import EventBus
from quantum_edge_core.market_data.config import HubConfig
from quantum_edge_core.market_data.feeds.base import BaseFeed
from quantum_edge_core.market_data.models import Priority

logger = logging.getLogger(__name__)

class LiquidationFeed(BaseFeed):
    """
    Connects to Binance Futures !forceOrder@arr stream.
    Filters by configured symbols and publishes LiquidationEvents.
    """
    
    WS_URL = "wss://fstream.binance.com/ws/!forceOrder@arr"

    def __init__(self, config: HubConfig, bus: EventBus) -> None:
        super().__init__(config, bus)
        self.symbols: Set[str] = set(config.symbols) # Fast lookup
        self._ws_task: asyncio.Task | None = None

    async def _run(self) -> None:
        """Main loop managing WebSocket connection."""
        while not self._stop_event.is_set():
            try:
                async with websockets.connect(self.WS_URL) as ws:
                    logger.info("Connected to Binance Liquidation Stream")
                    
                    while not self._stop_event.is_set():
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=60.0)
                            await self._handle_message(msg)
                        except asyncio.TimeoutError:
                            logger.warning("Liquidation stream timeout, reconnecting...")
                            break
                        except websockets.exceptions.ConnectionClosed:
                            logger.warning("Liquidation stream closed, reconnecting...")
                            break
                            
            except Exception as e:
                logger.error(f"Liquidation Feed Error: {e}")
                if not self._stop_event.is_set():
                    await asyncio.sleep(5)  # Backoff

    async def _handle_message(self, msg: str) -> None:
        """Parse and filter liquidation event."""
        try:
            payload = json.loads(msg)
            # Payload is a dict: {"e":"forceOrder", "o": {...}}
            data = payload.get("o")
            if not data:
                return

            symbol = data.get("s")
            if symbol not in self.symbols:
                return

            # Parse fields
            side = data.get("S") # SELL (Long Liquidated) or BUY (Short Liquidated)
            qty = float(data.get("q", 0.0))
            price = float(data.get("p", 0.0))
            avg_price = float(data.get("ap", 0.0)) # Average execution price
            trade_time = int(data.get("T", 0)) # ms

            # Use average price if available/nonzero for better accuracy, else limit price
            exec_price = avg_price if avg_price > 0 else price
            usd_size = exec_price * qty

            # Alert on large liquidations
            if usd_size > 50000:
                logger.warning(f"[RISK] WHALE LIQUIDATION: ${usd_size:,.0f} @ {price:.2f} ({symbol} {side})")

            # Normalize Event
            event = {
                "event_type": "liquidation",
                "symbol": symbol,
                "side": side,
                "price": exec_price,
                "qty": qty,
                "usd_size": usd_size,
                "timestamp": trade_time, # ms
                "received_at": time.time_ns(),
                "priority": Priority.L2
            }
            
            # Publish to Bus
            # Note: EventBus usually expects an object or dict. 
            # Hub dispatcher handles dicts if event_type is present.
            await self.bus.publish(event)

        except Exception as e:
            logger.error(f"Failed to process liquidation msg: {e}")

    async def stop(self) -> None:
        await super().stop()
        if self._ws_task:
            self._ws_task.cancel()
