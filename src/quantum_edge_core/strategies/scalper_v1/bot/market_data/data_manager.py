import asyncio
import time
from dataclasses import dataclass
from typing import Dict, Optional

from bot.storage.event_bus import EventPriority
from bot.storage.tsdb.sink import get_tsdb_sink
from bot.storage.tsdb_config import load_tsdb_config


class DataManager:
    """
    Buffered market data publishing for TSDB ingestion.

    - market_trades_raw: optional, behind config flag.
    - market_l1: best bid/ask + sizes.
    - bars_1s: aggregated from trades.
    """

    def __init__(self):
        self._cfg = load_tsdb_config()
        self._enabled = bool(self._cfg.enabled and self._cfg.backend == "questdb")
        self._events_cfg = self._cfg.events
        self._sink = get_tsdb_sink()
        self._bars = _Bars1sAggregator()

    # Public API
    async def save_trade(self, data: dict):
        if not self._enabled:
            return
        try:
            ts_ms = int(data.get("T") or data.get("E") or (time.time() * 1000))
            symbol = str(data.get("s", "UNKNOWN")).upper()
            price = float(data.get("p") or data.get("price") or 0.0)
            qty = float(data.get("q") or data.get("qty") or 0.0)
            side = "sell" if data.get("m") else "buy"
        except Exception:
            return

        if self._events_cfg.raw_trades:
            await self._sink.publish(
                {
                    "table": "market_trades_raw",
                    "symbol": symbol,
                    "price": price,
                    "qty": qty,
                    "side": side,
                    "trade_id": data.get("t") or data.get("tradeId"),
                    "ts": ts_ms,
                },
                priority=EventPriority.LOW,
            )

        if self._events_cfg.bars_1s:
            bar = self._bars.add_trade(symbol, ts_ms, price, qty)
            if bar:
                await self._sink.publish(bar, priority=EventPriority.NORMAL)

    async def save_orderbook(self, data: dict):
        if not self._enabled or not self._events_cfg.market_l1:
            return
        try:
            ts_ms = int(data.get("T") or data.get("E") or (time.time() * 1000))
            symbol = str(data.get("s", "UNKNOWN")).upper()
            bids = data.get("bids") or data.get("b") or []
            asks = data.get("asks") or data.get("a") or []
            best_bid = float(bids[0][0]) if bids else None
            best_ask = float(asks[0][0]) if asks else None
            bid_sz = float(bids[0][1]) if bids else None
            ask_sz = float(asks[0][1]) if asks else None
        except Exception:
            return
        await self._sink.publish(
            {
                "table": "market_l1",
                "symbol": symbol,
                "bid": best_bid,
                "ask": best_ask,
                "bid_sz": bid_sz,
                "ask_sz": ask_sz,
                "ts": ts_ms,
            },
            priority=EventPriority.NORMAL,
        )

    def close(self):
        # Best-effort flush; bus handles shutdown separately.
        if self._events_cfg.bars_1s and self._enabled:
            for bar in self._bars.flush_all():
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(
                        self._sink.publish(bar, priority=EventPriority.NORMAL)
                    )
                except Exception:
                    pass


@dataclass
class _BarState:
    sec: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    trades: int


class _Bars1sAggregator:
    def __init__(self) -> None:
        self._state: Dict[str, _BarState] = {}

    def add_trade(
        self, symbol: str, ts_ms: int, price: float, qty: float
    ) -> Optional[Dict[str, object]]:
        sec = int(ts_ms // 1000)
        state = self._state.get(symbol)
        if state and state.sec != sec:
            bar = {
                "table": "bars_1s",
                "symbol": symbol,
                "open": state.open,
                "high": state.high,
                "low": state.low,
                "close": state.close,
                "volume": state.volume,
                "trades": state.trades,
                "ts": state.sec * 1000,
            }
            self._state[symbol] = _BarState(
                sec=sec,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=qty,
                trades=1,
            )
            return bar
        if state is None:
            self._state[symbol] = _BarState(
                sec=sec,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=qty,
                trades=1,
            )
            return None
        state.high = max(state.high, price)
        state.low = min(state.low, price)
        state.close = price
        state.volume += qty
        state.trades += 1
        return None

    def flush_all(self) -> list[Dict[str, object]]:
        bars = []
        for symbol, state in list(self._state.items()):
            bars.append(
                {
                    "table": "bars_1s",
                    "symbol": symbol,
                    "open": state.open,
                    "high": state.high,
                    "low": state.low,
                    "close": state.close,
                    "volume": state.volume,
                    "trades": state.trades,
                    "ts": state.sec * 1000,
                }
            )
        self._state.clear()
        return bars
