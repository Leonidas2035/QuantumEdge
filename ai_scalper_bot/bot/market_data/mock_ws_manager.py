import asyncio
import random
import time
from typing import AsyncIterator, List, Dict


class MockWSManager:
    """
    Generates mock Binance-style trade events for development and paper trading.

    Event schema matches Binance trade payload:
        e: "trade"
        E: event timestamp (ms)
        T: trade timestamp (ms)
        s: symbol (e.g., "BTCUSDT")
        p: price (string)
        q: quantity (string)
        m: is_buyer_maker (taker sell boolean)
    """

    def __init__(self, symbols: List[str], delay_min: float = 0.01, delay_max: float = 0.05):
        self.symbols = symbols
        self.delay_min = delay_min
        self.delay_max = delay_max
        self.state = {sym: {"price": 45000.0, "spread": 0.5} for sym in symbols}
        self._rng = random.Random(42)

    def _tick(self, symbol: str) -> Dict:
        st = self.state[symbol]
        drift = self._rng.normalvariate(0, 1.5)
        st["price"] = max(1.0, st["price"] + drift)
        qty = max(0.0005, abs(self._rng.normalvariate(0.002, 0.0015)))
        ts = int(time.time() * 1000)
        return {
            "e": "trade",
            "E": ts,
            "T": ts,
            "s": symbol,
            "p": f"{st['price']:.2f}",
            "q": f"{qty:.6f}",
            "m": self._rng.choice([True, False]),
        }

    async def stream(self) -> AsyncIterator[Dict]:
        while True:
            for sym in self.symbols:
                yield self._tick(sym)
                await asyncio.sleep(self._rng.uniform(self.delay_min, self.delay_max))
