"""
SIGNAL BACKTEST CLI — на РЕАЛЬНИХ кешованих даних BTC (Bybit, рік).
Використання (з /home/layman/QuantumEdge):
  .venv/bin/python3 -m tools.sigtest.cli --horizons 6,12,24,48
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.sigtest.indicators import enrich
from tools.sigtest.engine import backtest_signals

QE = Path("/home/layman/QuantumEdge")
CACHE = QE / "runtime/cache/ohlcv_BTC_USDT_USDT_1h_bybit_365d.json"


def load_df():
    raw = json.loads(CACHE.read_text())
    rows = raw if isinstance(raw, list) else list(raw.values())[0]
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizons", default="6,12,24,48")
    ap.add_argument("--warmup", type=int, default=60)
    ap.add_argument("--out", default="runtime/sigtest_calibration.json")
    args = ap.parse_args()

    df = load_df()
    df = enrich(df)
    print(f"data: {len(df)} candles (BTC 1h, Bybit cache)")

    horizons = [int(x) for x in args.horizons.split(",")]
    results = {}
    for h in horizons:
        res = backtest_signals(df, horizon=h, warmup=args.warmup)
        results[h] = res
        print(f"\n===== HORIZON {h}h =====")
        print(f"{'signal':14s} {'n':>6s} {'win_rate':>9s} {'avg_ret%':>9s}  L/S")
        print("  -- окремі індикатори --")
        for r in res["per_rule"]:
            print(f"{r['signal']:14s} {r['n']:>6d} {r['win_rate']:>9.1%} "
                  f"{r['avg_dir_return_pct']:>9.3f}  {r['long_n']}/{r['short_n']}")
        print("  -- композит (поріг голосів) --")
        for r in res["composite"]:
            print(f"th>={r['signal']:10} {r['n']:>6d} {r['win_rate']:>9.1%} "
                  f"{r['avg_dir_return_pct']:>9.3f}  {r['long_n']}/{r['short_n']}")

    (QE / args.out).write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"\nsaved -> {QE / args.out}")


if __name__ == "__main__":
    main()
