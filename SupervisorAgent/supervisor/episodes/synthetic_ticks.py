"""Synthetic tick generator for offline episode validation."""

from __future__ import annotations

import json
import random
import time
from pathlib import Path


def write_synthetic_ticks(
    path: Path,
    count: int = 500,
    seed: int = 42,
    start_ts: float | None = None,
    symbol: str = "BTCUSDT",
) -> Path:
    rng = random.Random(seed)
    ts = start_ts or time.time()
    price = 100.0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for i in range(count):
            drift = rng.uniform(-0.05, 0.05)
            price = max(price + drift, 1.0)
            spread = max(rng.uniform(0.01, 0.05), 0.001)
            bid = price - spread / 2
            ask = price + spread / 2
            tick = {
                "ts": ts + i * 0.1,
                "price": price,
                "qty": rng.uniform(0.01, 1.0),
                "side": "buy" if rng.random() > 0.5 else "sell",
                "bid": bid,
                "ask": ask,
                "symbol": symbol,
            }
            handle.write(json.dumps(tick, ensure_ascii=True))
            handle.write("\n")
    return path
