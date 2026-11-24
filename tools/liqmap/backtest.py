"""
================================================================
QuantumEdge — LIQ-ZONE BACKTEST (тренування впевненості сигналу)
================================================================
Питання, на яке відповідає: коли зона ліквідації має інтенсивність >= X,
з якою ймовірністю ціна ТОРКНЕТЬСЯ її протягом N годин/днів?

Це і є "тренування" скіла: перетворюємо евристичну карту на
статистику hit-rate, щоб знати, яким сигналам довіряти.

МЕТОД (walk-forward, без заглядання в майбутнє):
  1. Беремо історію годинних свічок BTC.
  2. У кожній точці t (крок = step) будуємо карту ЛИШЕ з даних [0..t]
     (як бачив би бот у реальному часі).
  3. Витягуємо зони; для кожної зони з intensity>=bucket дивимось,
     чи ціна торкнулась її ціни у вікні (t, t+horizon].
  4. Рахуємо hit-rate по бакетах інтенсивності та по стороні (LONG/SHORT).
  5. Також median time-to-hit (скільки годин до торкання).
================================================================
"""
from __future__ import annotations
import sys
import json
import argparse
import statistics as st
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.liqmap.datafeed import fetch_ohlcv_history, fetch_oi_history_long
from tools.liqmap.model import build_heatmap, classify_regime

BUCKETS = [(1.0, 1.01), (0.8, 1.0), (0.6, 0.8), (0.4, 0.6), (0.2, 0.4)]
DIST_BUCKETS = ["0-1%", "1-2%", "2-3%", "3-5%", "5%+" ]


def _bucket(v):
    for lo, hi in BUCKETS:
        if lo <= v < hi:
            return f"{lo:.1f}-{hi:.2f}"
    return None


def _dist_bucket(dist):
    ad = abs(dist)
    if ad <= 1.0:
        return "0-1%"
    elif ad <= 2.0:
        return "1-2%"
    elif ad <= 3.0:
        return "2-3%"
    elif ad <= 5.0:
        return "3-5%"
    else:
        return "5%+"


def pool_adjacent_violators(y, w=None):
    """
    Алгоритм PAVA для ізотонічної регресії.
    y: список початкових значень (hit-rates).
    w: ваги (обсяги вибірок n).
    Повертає монотонно неспадно згладжений список.
    """
    if w is None:
        w = [1.0] * len(y)
    
    n = len(y)
    if n == 0:
        return []
        
    blocks = []
    for i in range(n):
        blocks.append([y[i], w[i], [i]])
        
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
        new_indices = b1[2] + b2[2]
        
        blocks[violator_idx] = [new_val, new_w, new_indices]
        blocks.pop(violator_idx + 1)
        
    out = [0.0] * n
    for val, _, indices in blocks:
        for idx in indices:
            out[idx] = val
    return out


def calibrate_pava(regime_data):
    """Калібрує hit_rates за допомогою PAVA для забезпечення монотонності."""
    # Групуємо елементи за (distance_bucket, side)
    groups = {}
    for item in regime_data:
        k = (item["distance_bucket"], item["side"])
        groups.setdefault(k, []).append(item)
        
    for k, items in groups.items():
        bucket_order = ["0.2-0.40", "0.4-0.60", "0.6-0.80", "0.8-1.00", "1.0-1.01"]
        ordered_items = []
        for b in bucket_order:
            match = [it for it in items if it["bucket"] == b]
            if match:
                ordered_items.append(match[0])
            else:
                ordered_items.append({
                    "distance_bucket": k[0],
                    "bucket": b,
                    "side": k[1],
                    "n": 0,
                    "hit_rate": 0.0,
                    "median_hours_to_hit": None,
                    "avg_favorable_move_pct": 0.0,
                    "is_dummy": True
                })
                
        y = [it["hit_rate"] for it in ordered_items]
        w = [max(1, it["n"]) for it in ordered_items]
        
        calibrated_y = pool_adjacent_violators(y, w)
        
        for it, cal_val in zip(ordered_items, calibrated_y):
            it["calibrated_confidence"] = round(cal_val, 3)
            
        # Оновлюємо початкові елементи (без dummy)
        for it in ordered_items:
            if it.get("is_dummy"):
                continue
            # Зворотний запис
            for orig in items:
                if orig["bucket"] == it["bucket"]:
                    orig["calibrated_confidence"] = it["calibrated_confidence"]
                    
    # Для елементів, які не отримали calibrated_confidence через відсутність у групі
    for item in regime_data:
        if "calibrated_confidence" not in item:
            item["calibrated_confidence"] = item["hit_rate"]
            
    return regime_data


def backtest(ohlcv, horizon=72, warmup=120, step=6, bins=140, oi_series=None,
             max_dist_pct=None):
    """
    horizon: скільки свічок (годин) уперед перевіряти торкання.
    warmup: мін. історія перед першою точкою.
    step: крок по часу.
    max_dist_pct: якщо задано, рахувати лише зони не далі цього % від ціни.
    """
    n = len(ohlcv)
    hi = [c[2] for c in ohlcv]
    lo = [c[3] for c in ohlcv]
    
    regimes = ["all", "bull", "bear", "flat"]
    stats = {r: {} for r in regimes}

    t = warmup
    while t + 1 < n:
        window = ohlcv[:t]
        oi_win = None
        if oi_series:
            oi_win = [x for x in oi_series if x[0] <= ohlcv[t - 1][0]]
            if len(oi_win) < 2:
                oi_win = None
        
        hm = build_heatmap(window, price_bins=bins, oi_series=oi_win)
        current_regime = classify_regime(ohlcv, t)
        
        future_hi = hi[t: t + horizon]
        future_lo = lo[t: t + horizon]
        if not future_hi:
            break
            
        last_price = ohlcv[t-1][4]
        
        for z in hm["zones"]:
            if max_dist_pct is not None and abs(z["dist_pct"]) > max_dist_pct:
                continue
                
            b = _bucket(z["intensity"])
            d = _dist_bucket(z["dist_pct"])
            if b is None or d is None:
                continue
                
            price = z["price"]
            
            if z["side"] == "SHORT_LIQ":
                fav_move = (max(future_hi) - last_price) / last_price * 100.0
            else:
                fav_move = (last_price - min(future_lo)) / last_price * 100.0

            for r in ["all", current_regime]:
                key = (d, b, z["side"])
                stats[r].setdefault(key, {"hit": 0, "miss": 0, "tth": [], "fav_moves": []})
                
                hit_idx = None
                for k in range(len(future_hi)):
                    if future_lo[k] <= price <= future_hi[k]:
                        hit_idx = k
                        break
                        
                if hit_idx is not None:
                    stats[r][key]["hit"] += 1
                    stats[r][key]["tth"].append(hit_idx + 1)
                else:
                    stats[r][key]["miss"] += 1
                
                stats[r][key]["fav_moves"].append(fav_move)
                
        t += step

    out = {}
    for r in regimes:
        regime_data = []
        for (d, b, side), s in sorted(stats[r].items()):
            total = s["hit"] + s["miss"]
            if total == 0:
                continue
            regime_data.append({
                "distance_bucket": d,
                "bucket": b,
                "side": side,
                "n": total,
                "hit_rate": round(s["hit"] / total, 3),
                "median_hours_to_hit": round(st.median(s["tth"]), 1) if s["tth"] else None,
                "avg_favorable_move_pct": round(sum(s["fav_moves"]) / len(s["fav_moves"]), 3) if s["fav_moves"] else 0.0
            })
        # Калібруємо PAVA для монотонності
        out[r] = calibrate_pava(regime_data)
        
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="1h")
    ap.add_argument("--days", type=int, default=180, help="кількість днів історії")
    ap.add_argument("--horizon", type=int, default=72, help="вікно перевірки торкання (год)")
    ap.add_argument("--step", type=int, default=6)
    ap.add_argument("--warmup", type=int, default=120)
    ap.add_argument("--bins", type=int, default=140)
    ap.add_argument("--max-dist", type=float, default=3.0,
                    help="рахувати лише зони не далі цього % від ціни (напр. 3.0)")
    ap.add_argument("--refresh", action="store_true", help="ігнорувати кеш")
    ap.add_argument("--regime", default="all", choices=["all", "bull", "bear", "flat"], help="показати деталі для режиму")
    ap.add_argument("--out", default="runtime/liqmap_calibration.json")
    args = ap.parse_args()

    print(f"Loading history for {args.days} days...")
    ohlcv, src = fetch_ohlcv_history(timeframe=args.tf, days=args.days, refresh=args.refresh)
    oi, oisrc = fetch_oi_history_long(timeframe=args.tf, days=args.days, refresh=args.refresh)
    
    oi_len = len(oi) if oi else 0
    print(f"Loaded: {len(ohlcv)} candles (OHLCV source: {src}), {oi_len} OI points (OI source: {oisrc})")
    
    print("Running backtest walk-forward simulation...")
    res = backtest(ohlcv, horizon=args.horizon, warmup=args.warmup,
                   step=args.step, bins=args.bins, oi_series=oi,
                   max_dist_pct=args.max_dist)

    qe = Path("/home/layman/QuantumEdge")
    outp = qe / args.out
    
    payload = {
        "generated_from_candles": len(ohlcv),
        "tf": args.tf,
        "horizon_hours": args.horizon,
        "ohlcv_source": src,
        "oi_source": oisrc,
        "regimes": res,
    }
    
    outp.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"Saved calibration results to {outp}")

    # Вивід результатів у табличному вигляді
    print(f"\nCALIBRATION FOR REGIME: '{args.regime}' (max_dist={args.max_dist}%, horizon={args.horizon}h):")
    print(f"{'dist_bucket':12s} {'bucket':10s} {'side':10s} {'n':>5s} {'hit_rate':>9s} {'cal_conf':>9s} {'med_h':>6s}")
    
    regime_results = res.get(args.regime, [])
    for r in regime_results:
        print(f"{r['distance_bucket']:12s} {r['bucket']:10s} {r['side']:10s} {r['n']:>5d} "
              f"{r['hit_rate']:>9.1%} {r['calibrated_confidence']:>9.1%} {str(r['median_hours_to_hit']):>6s}")


if __name__ == "__main__":
    main()
