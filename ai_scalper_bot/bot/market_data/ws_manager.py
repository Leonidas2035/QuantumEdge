import asyncio
import json
import traceback
from typing import Any, Dict, Optional

import websockets

from bot.core.config_loader import config
from bot.market_data.data_manager import DataManager


class WSManager:
    def __init__(self):
        self.base_ws = config.get("binance.ws_spot_base")  # wss://stream.binance.com:9443
        self.symbols = config.get("binance.symbols")
        self.data_manager = DataManager()
        self._last_depth: Dict[str, Dict[str, float]] = {}
        self._event_queue: Optional[asyncio.Queue] = None
        self._backoff_base = 1.0
        self._backoff_cap = 30.0

    def _build_stream_url(self) -> str:
        streams = []
        for sym in self.symbols:
            s = sym.lower()
            streams.append(f"{s}@trade")
            streams.append(f"{s}@depth20@100ms")

        stream_str = "/".join(streams)
        return f"{self.base_ws}/stream?streams={stream_str}"

    async def _emit(self, event: Dict[str, Any]) -> None:
        if self._event_queue:
            await self._event_queue.put(event)

    def _enrich_with_depth(self, symbol: str, event: Dict[str, Any]) -> Dict[str, Any]:
        depth = self._last_depth.get(symbol.upper()) or {}
        if depth:
            event.setdefault("b", depth.get("bid"))
            event.setdefault("a", depth.get("ask"))
            event.setdefault("depth", depth.get("depth_usd"))
        return event

    async def connect(self) -> None:
        url = self._build_stream_url()
        backoff = self._backoff_base
        attempt = 0

        while True:
            try:
                attempt += 1
                print(f"[WS] Connecting to: {url} (attempt {attempt})")
                async with websockets.connect(url, ping_interval=20) as ws:
                    print("[WS] Connected.")
                    backoff = self._backoff_base
                    attempt = 0
                    async for msg in ws:
                        await self.process_message(msg)
                print(f"[WS] Disconnected; reconnecting in {backoff:.1f}s")
            except asyncio.CancelledError:
                raise
            except Exception as e:  # pragma: no cover - network path
                print(f"[WS] Error: {e}; reconnecting in {backoff:.1f}s (cap {self._backoff_cap}s)")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, self._backoff_cap)

    async def process_message(self, msg: str) -> None:
        try:
            data = json.loads(msg)
            stream = data.get("stream")
            payload = data.get("data")
            if not payload or not stream:
                return

            if "depth" in stream:
                await self.data_manager.save_orderbook(payload)
                bids = payload.get("bids") or payload.get("b") or []
                asks = payload.get("asks") or payload.get("a") or []
                best_bid = float(bids[0][0]) if bids else None
                best_ask = float(asks[0][0]) if asks else None
                qty_bid = float(bids[0][1]) if bids else 0.0
                qty_ask = float(asks[0][1]) if asks else 0.0
                depth_usd = 0.0
                if best_bid:
                    depth_usd += best_bid * qty_bid
                if best_ask:
                    depth_usd += best_ask * qty_ask
                self._last_depth[payload.get("s", "").upper()] = {
                    "bid": best_bid or 0.0,
                    "ask": best_ask or 0.0,
                    "depth_usd": depth_usd,
                }
                return

            if "trade" in stream:
                symbol = payload.get("s", "UNKNOWN").upper()
                await self.data_manager.save_trade(payload)
                enriched = self._enrich_with_depth(symbol, payload)
                await self._emit(enriched)
        except Exception:
            traceback.print_exc()

    async def stream(self):
        """
        Async generator that yields trade events enriched with best bid/ask/depth when available.
        """
        self._event_queue = asyncio.Queue()
        asyncio.create_task(self.connect())
        while True:
            evt = await self._event_queue.get()
            yield evt
