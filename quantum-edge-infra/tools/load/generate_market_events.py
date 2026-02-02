from __future__ import annotations

import argparse
import asyncio
import random
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Sequence


def _bootstrap_paths() -> None:
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    bot_root = root / "ai_scalper_bot"
    if bot_root.exists() and str(bot_root) not in sys.path:
        sys.path.insert(0, str(bot_root))


_bootstrap_paths()

from bot.storage.event_bus import EventBus, EventPriority  # noqa: E402


SYMBOLS_DEFAULT = [
    "BTCUSDT",
    "ETHUSDT",
    "BNBUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "ADAUSDT",
    "AVAXUSDT",
    "DOGEUSDT",
    "MATICUSDT",
    "LTCUSDT",
]


@dataclass
class GeneratorStats:
    trades: int = 0
    l1_updates: int = 0
    dropped: int = 0


class MarketEventGenerator:
    def __init__(
        self,
        symbols: Sequence[str],
        trades_per_sec: float,
        l1_per_sec: float,
        seed: int | None = None,
    ) -> None:
        self.symbols = list(symbols)
        self.trades_per_sec = float(trades_per_sec)
        self.l1_per_sec = float(l1_per_sec)
        self.stats = GeneratorStats()
        self._rng = random.Random(seed)
        self._prices = {
            symbol: self._seed_price(symbol, idx)
            for idx, symbol in enumerate(self.symbols)
        }
        self._trade_ids = {symbol: 0 for symbol in self.symbols}

    def _seed_price(self, symbol: str, idx: int) -> float:
        base = 50.0 + idx * 5.0
        if "BTC" in symbol:
            base = 30000.0
        elif "ETH" in symbol:
            base = 2000.0
        elif "BNB" in symbol:
            base = 300.0
        return base

    def _tick_price(self, symbol: str) -> float:
        price = self._prices.get(symbol, 100.0)
        drift = self._rng.uniform(-0.002, 0.002)
        price = max(price * (1.0 + drift), 0.01)
        self._prices[symbol] = price
        return price

    def _now_ms(self) -> int:
        return int(time.time() * 1000)

    def _trade_event(self, symbol: str) -> dict:
        price = self._tick_price(symbol)
        self._trade_ids[symbol] += 1
        side = "buy" if self._rng.random() > 0.5 else "sell"
        qty = round(self._rng.uniform(0.01, 1.5), 6)
        return {
            "table": "market_trades_raw",
            "symbol": symbol,
            "side": side,
            "price": round(price, 6),
            "qty": qty,
            "trade_id": self._trade_ids[symbol],
            "ts": self._now_ms(),
        }

    def _l1_event(self, symbol: str) -> dict:
        price = self._tick_price(symbol)
        spread = max(price * 0.0002, 0.0001)
        bid = price - spread
        ask = price + spread
        return {
            "table": "market_l1",
            "symbol": symbol,
            "bid": round(bid, 6),
            "ask": round(ask, 6),
            "bid_sz": round(self._rng.uniform(0.1, 5.0), 6),
            "ask_sz": round(self._rng.uniform(0.1, 5.0), 6),
            "ts": self._now_ms(),
        }

    async def _emit_loop(
        self,
        bus: EventBus,
        symbol: str,
        rate: float,
        builder,
        priority: EventPriority,
        counter: str,
        stop_event: asyncio.Event,
    ) -> None:
        if rate <= 0:
            return
        interval = 1.0 / rate
        next_tick = time.perf_counter()
        while not stop_event.is_set():
            event = builder(symbol)
            ok = await bus.publish(event, priority=priority)
            if counter == "trades":
                self.stats.trades += 1
            else:
                self.stats.l1_updates += 1
            if not ok:
                self.stats.dropped += 1
            next_tick += interval
            sleep_for = max(0.0, next_tick - time.perf_counter())
            if sleep_for:
                await asyncio.sleep(sleep_for)

    async def run(self, bus: EventBus, stop_event: asyncio.Event) -> None:
        tasks: List[asyncio.Task] = []
        for symbol in self.symbols:
            tasks.append(
                asyncio.create_task(
                    self._emit_loop(
                        bus,
                        symbol,
                        self.trades_per_sec,
                        self._trade_event,
                        EventPriority.LOW,
                        "trades",
                        stop_event,
                    )
                )
            )
            tasks.append(
                asyncio.create_task(
                    self._emit_loop(
                        bus,
                        symbol,
                        self.l1_per_sec,
                        self._l1_event,
                        EventPriority.NORMAL,
                        "l1",
                        stop_event,
                    )
                )
            )
        try:
            await stop_event.wait()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


async def _drain_bus(bus: EventBus, stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(bus.get(), timeout=0.5)
        except asyncio.TimeoutError:
            continue


def _parse_symbols(value: str | None) -> List[str]:
    if not value:
        return SYMBOLS_DEFAULT
    symbols = [item.strip().upper() for item in value.split(",") if item.strip()]
    return symbols or SYMBOLS_DEFAULT


async def _run_cli(args: argparse.Namespace) -> int:
    bus = EventBus(max_events=args.queue_max_events, max_bytes=args.queue_max_bytes)
    stop_event = asyncio.Event()
    generator = MarketEventGenerator(
        symbols=_parse_symbols(args.symbols),
        trades_per_sec=args.trades_per_sec,
        l1_per_sec=args.l1_per_sec,
        seed=args.seed,
    )
    tasks = [asyncio.create_task(generator.run(bus, stop_event))]
    if args.drain:
        tasks.append(asyncio.create_task(_drain_bus(bus, stop_event)))
    await asyncio.sleep(args.duration_sec)
    stop_event.set()
    await asyncio.gather(*tasks, return_exceptions=True)
    snapshot = bus.snapshot()
    print(
        f"[generator] trades={generator.stats.trades} l1={generator.stats.l1_updates} dropped={generator.stats.dropped} "
        f"queue_events={snapshot['events']} queue_bytes={snapshot['bytes']}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate synthetic market events into EventBus.")
    parser.add_argument("--duration-sec", type=float, default=10.0, help="Runtime duration.")
    parser.add_argument("--trades-per-sec", type=float, default=5.0, help="Trades/sec per symbol.")
    parser.add_argument("--l1-per-sec", type=float, default=5.0, help="L1 updates/sec per symbol.")
    parser.add_argument("--symbols", help="Comma-separated symbols (default 10).")
    parser.add_argument("--seed", type=int, help="Random seed.")
    parser.add_argument("--queue-max-events", type=int, default=10000, help="EventBus max events.")
    parser.add_argument(
        "--queue-max-bytes",
        type=int,
        default=256 * 1024 * 1024,
        help="EventBus max bytes.",
    )
    parser.add_argument("--drain", action="store_true", help="Drain the EventBus while generating.")
    args = parser.parse_args()
    return asyncio.run(_run_cli(args))


if __name__ == "__main__":
    raise SystemExit(main())
