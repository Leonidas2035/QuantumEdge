"""
================================================================
QuantumEdge — COMPOSITE SIGNAL BACKTESTER
================================================================
Проводить walk-forward симуляцію інтегрального сигналу на історії.
Обчислює hit-rate досягнення цілі 1.5 ATR (з обмеженням стоп-лоссу 1.5 ATR)
протягом заданого горизонту (наприклад, 48 годин).

Калібрує фінальні ймовірності (calibrated_confidence) через PAVA
для забезпечення монотонності.
================================================================
"""
from __future__ import annotations
import sys
import json
import argparse
import statistics as st
import numpy as np
import pandas as pd
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.liqmap.datafeed import fetch_ohlcv_history, fetch_oi_history_long
from tools.liqmap.model import build_heatmap, classify_regime
from tools.composite_signal.engine import calculate_composite_score

SCORE_BUCKETS = [
    (80, 101, "80 to 100"),
    (40, 80, "40 to 80"),
    (10, 40, "10 to 40"),
    (-10, 10, "-10 to 10"),
    (-40, -10, "-40 to -10"),
    (-80, -40, "-80 to -40"),
    (-101, -80, "-100 to -80")
]


def _score_bucket(score):
    for lo, hi, name in SCORE_BUCKETS:
        if lo <= score < hi:
            return name
    return "-10 to 10"


def pool_adjacent_violators(y, w=None):
    if w is None:
        w = [1.0] * len(y)
    n = len(y)
    if n == 0:
        return []
    blocks = [[y[i], w[i], [i]] for i in range(n)]
    while True:
        violator_idx = None
        for i in range(len(blocks) - 1):
            if blocks[i][0] > blocks[i+1][0]:
                violator_idx = i
                break
        if violator_idx is None:
            break
        b1 = blocks[violator_idx]
        b2 = blocks[violator_idx + 1]
        new_w = b1[1] + b2[1]
        new_val = (b1[0] * b1[1] + b2[0] * b2[1]) / new_w if new_w else 0.0
        blocks[violator_idx] = [new_val, new_w, b1[2] + b2[2]]
        blocks.pop(violator_idx + 1)
    out = [0.0] * n
    for val, _, indices in blocks:
        for idx in indices:
            out[idx] = val
    return out


def calibrate_pava_composite(regime_data):
    """Калібрує впевненість для LONG (позитивні бакети) та SHORT (негативні бакети) окремо."""
    groups = {}
    for item in regime_data:
        groups.setdefault(item["side"], []).append(item)

    for side, items in groups.items():
        if side == "LONG":
            bucket_order = ["10 to 40", "40 to 80", "80 to 100"]
        else:
            bucket_order = ["-40 to -10", "-80 to -40", "-100 to -80"]

        ordered_items = []
        for b in bucket_order:
            match = [it for it in items if it["bucket"] == b]
            if match:
                ordered_items.append(match[0])
            else:
                ordered_items.append({
                    "bucket": b,
                    "side": side,
                    "n": 0,
                    "hit_rate": 0.0,
                    "is_dummy": True
                })

        y = [it["hit_rate"] for it in ordered_items]
        w = [max(1, it["n"]) for it in ordered_items]
        calibrated_y = pool_adjacent_violators(y, w)

        for it, cal_val in zip(ordered_items, calibrated_y):
            it["calibrated_confidence"] = round(cal_val, 3)

        for it in ordered_items:
            if it.get("is_dummy"):
                continue
            for orig in items:
                if orig["bucket"] == it["bucket"]:
                    orig["calibrated_confidence"] = it["calibrated_confidence"]

    for item in regime_data:
        if "calibrated_confidence" not in item:
            item["calibrated_confidence"] = item["hit_rate"]
    return regime_data


def backtest_composite(ohlcv, horizon=48, warmup=120, step=6, bins=140, oi_series=None):
    """
    Проганяє walk-forward бектест композитного сигналу.
    Ціль: 1.5 ATR. Стоп: 1.5 ATR.
    """
    n = len(ohlcv)
    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    hi = df["high"].values
    lo = df["low"].values
    cl = df["close"].values

    regimes = ["all", "bull", "bear", "flat"]
    stats = {r: {} for r in regimes}

    t = warmup
    while t + 1 < n:
        df_win = df.iloc[:t]
        oi_win = None
        if oi_series:
            oi_win = [x for x in oi_series if x[0] <= ohlcv[t-1][0]]

        # Будуємо heatmap для 5-ї осі (liq_zones)
        hm = build_heatmap(ohlcv[:t], price_bins=bins, oi_series=oi_win)
        current_regime = classify_regime(ohlcv, t)

        res = calculate_composite_score(df_win, funding_rate=0.0, oi_series=oi_win, liq_zones=hm["zones"])
        score = res["score"]
        atr = res["atr"]

        if abs(score) <= 10 or atr <= 0:
            t += step
            continue

        side = "LONG" if score > 10 else "SHORT"
        b = _score_bucket(score)
        entry_price = cl[t - 1]

        # Ціль та стоп
        if side == "LONG":
            target = entry_price + 1.5 * atr
            stop = entry_price - 1.5 * atr
        else:
            target = entry_price - 1.5 * atr
            stop = entry_price + 1.5 * atr

        future_hi = hi[t : t + horizon]
        future_lo = lo[t : t + horizon]
        if len(future_hi) == 0:
            break

        hit_idx = None
        stop_idx = None

        for k in range(len(future_hi)):
            # Перевіряємо торкання стопа чи цілі
            if side == "LONG":
                if future_hi[k] >= target:
                    hit_idx = k
                    break
                if future_lo[k] <= stop:
                    stop_idx = k
                    break
            else:
                if future_lo[k] <= target:
                    hit_idx = k
                    break
                if future_hi[k] >= stop:
                    stop_idx = k
                    break

        hit = 1 if (hit_idx is not None and (stop_idx is None or hit_idx <= stop_idx)) else 0
        tth = (hit_idx + 1) if hit == 1 else None

        for r in ["all", current_regime]:
            key = (b, side)
            stats[r].setdefault(key, {"hit": 0, "miss": 0, "tth": []})
            if hit == 1:
                stats[r][key]["hit"] += 1
                stats[r][key]["tth"].append(tth)
            else:
                stats[r][key]["miss"] += 1

        t += step

    out = {}
    for r in regimes:
        regime_data = []
        for (b, side), s in sorted(stats[r].items()):
            total = s["hit"] + s["miss"]
            if total == 0:
                continue
            regime_data.append({
                "bucket": b,
                "side": side,
                "n": total,
                "hit_rate": round(s["hit"] / total, 3),
                "median_hours_to_hit": round(st.median(s["tth"]), 1) if s["tth"] else None
            })
        out[r] = calibrate_pava_composite(regime_data)

    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="1h")
    ap.add_argument("--days", type=int, default=180, help="кількість днів історії")
    ap.add_argument("--horizon", type=int, default=48, help="горизонт утримання позиції (год)")
    ap.add_argument("--step", type=int, default=6)
    ap.add_argument("--warmup", type=int, default=120)
    ap.add_argument("--bins", type=int, default=140)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--regime", default="all", choices=["all", "bull", "bear", "flat"])
    ap.add_argument("--out", default="runtime/composite_calibration.json")
    args = ap.parse_args()

    print(f"Loading history for {args.days} days...")
    ohlcv, src = fetch_ohlcv_history(timeframe=args.tf, days=args.days, refresh=args.refresh)
    oi, oisrc = fetch_oi_history_long(timeframe=args.tf, days=args.days, refresh=args.refresh)

    print(f"Loaded: {len(ohlcv)} candles, {len(oi)} OI points. Starting composite signal backtest...")
    res = backtest_composite(ohlcv, horizon=args.horizon, warmup=args.warmup,
                             step=args.step, bins=args.bins, oi_series=oi)

    qe = Path("/home/layman/QuantumEdge")
    outp = qe / args.out
    
    payload = {
        "generated_from_candles": len(ohlcv),
        "tf": args.tf,
        "horizon_hours": args.horizon,
        "ohlcv_source": src,
        "oi_source": oisrc,
        "regimes": res
    }
    
    outp.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"Saved composite calibration results to {outp}")

    print(f"\nCOMPOSITE SIGNAL CALIBRATION FOR REGIME: '{args.regime}' (horizon={args.horizon}h, target=1.5 ATR):")
    print(f"{'bucket':15s} {'side':6s} {'n':>5s} {'hit_rate':>9s} {'cal_conf':>9s} {'med_h':>6s}")
    
    for r in res.get(args.regime, []):
        print(f"{r['bucket']:15s} {r['side']:6s} {r['n']:>5d} "
              f"{r['hit_rate']:>9.1%} {r['calibrated_confidence']:>9.1%} {str(r['median_hours_to_hit']):>6s}")


if __name__ == "__main__":
    main()
