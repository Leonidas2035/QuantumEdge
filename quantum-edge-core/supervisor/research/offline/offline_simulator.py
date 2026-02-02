import os
import time
import json
import argparse
from dataclasses import dataclass
from typing import Optional, Iterator

import pandas as pd


TRADES_DIR = os.path.join("data", "trades")
ORDERBOOKS_DIR = os.path.join("data", "orderbooks")
OFFLINE_DIR = os.path.join("data", "offline")


@dataclass
class Tick:
    ts: int
    price: float
    qty: float
    side: str
    bid: float
    ask: float


class OfflineTickSource:

    def __init__(self, symbol: str):
        self.symbol = symbol.upper()
        fname = f"{self.symbol}_ticks.csv"
        self.path = os.path.join(OFFLINE_DIR, fname)

        if not os.path.exists(self.path):
            raise FileNotFoundError(f"Offline data file not found: {self.path}")

        self.df = pd.read_csv(self.path)

        required = ["timestamp", "price"]
        for r in required:
            if r not in self.df.columns:
                raise ValueError(f"CSV must have '{r}' column")

        self.df["qty"] = self.df.get("qty", 0.001)
        self.df["side"] = self.df.get("side", "buy")
        self.df["bid"] = self.df.get("bid", self.df["price"])
        self.df["ask"] = self.df.get("ask", self.df["price"])

        self.df = self.df.sort_values("timestamp").reset_index(drop=True)

    def ticks(self) -> Iterator[Tick]:
        for _, r in self.df.iterrows():
            yield Tick(
                ts=int(r["timestamp"]),
                price=float(r["price"]),
                qty=float(r["qty"]),
                side=str(r["side"]),
                bid=float(r["bid"]),
                ask=float(r["ask"]),
            )


class OfflineSimulator:

    def __init__(self, symbol: str, speed: float = 0):
        self.symbol = symbol.upper()
        self.speed = speed

        self.source = OfflineTickSource(self.symbol)

        os.makedirs(TRADES_DIR, exist_ok=True)
        os.makedirs(ORDERBOOKS_DIR, exist_ok=True)

    def prepare_ticks(self):
        """
        Normalize tick timestamps so we have enough data to build features.
        FeatureBuilder expects >=10 trades and multiple 1s OHLCV buckets, so we
        space ticks 1s apart and pad if needed.
        """
        ticks = list(self.source.ticks())
        if not ticks:
            return []

        base_ts = ticks[0].ts

        # Ensure strictly increasing timestamps
        prepared = []
        last_ts = None
        for t in ticks:
            ts = int(t.ts)
            if last_ts is not None and ts <= last_ts:
                ts = last_ts + 1
            prepared.append(Tick(ts=ts, price=t.price, qty=t.qty, side=t.side, bid=t.bid, ask=t.ask))
            last_ts = ts

        # Spread ticks into 1s buckets if they are too dense (e.g., all within the same second)
        unique_seconds = len({t.ts // 1000 for t in prepared})
        if unique_seconds < 3:
            prepared = [
                Tick(
                    ts=base_ts + i * 1000,
                    price=t.price,
                    qty=t.qty,
                    side=t.side,
                    bid=t.bid,
                    ask=t.ask
                )
                for i, t in enumerate(prepared)
            ]

        # Pad trades so FeatureBuilder has enough history
        while len(prepared) < 10:
            last = prepared[-1]
            prepared.append(
                Tick(
                    ts=last.ts + 1000,
                    price=last.price,
                    qty=last.qty,
                    side=last.side,
                    bid=last.bid,
                    ask=last.ask
                )
            )

        return prepared

    def run(self):
        ticks = self.prepare_ticks()
        if not ticks:
            print("[SIM] No offline ticks found.")
            return

        print(f"[SIM] Replay start for {self.symbol} ({len(ticks)} ticks)")

        last_ts = None

        for tick in ticks:

            # WS-style filename pattern
            trade_path = os.path.join(TRADES_DIR, f"{self.symbol}_{tick.ts}.json")
            ob_path = os.path.join(ORDERBOOKS_DIR, f"{self.symbol}_{tick.ts}.json")

            # save trade
            trade_event = {
                "T": tick.ts,
                "p": tick.price,
                "q": tick.qty,
                "m": tick.side == "sell",
                "s": self.symbol
            }
            with open(trade_path, "w") as f:
                json.dump(trade_event, f)

            # save orderbook
            ob_event = {
                "E": tick.ts,
                "bids": [[tick.bid, tick.qty]],
                "asks": [[tick.ask, tick.qty]],
                "s": self.symbol
            }
            with open(ob_path, "w") as f:
                json.dump(ob_event, f)

            # timing
            if last_ts is not None and self.speed > 0:
                dt = tick.ts - last_ts
                delay = dt / 1000.0 / self.speed
                if delay > 0:
                    time.sleep(delay)

            last_ts = tick.ts

        print("[SIM] Replay done")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", type=str, default="BTCUSDT")
    parser.add_argument("--speed", type=float, default=0)
    args = parser.parse_args()

    OfflineSimulator(symbol=args.symbol, speed=args.speed).run()


if __name__ == "__main__":
    main()
