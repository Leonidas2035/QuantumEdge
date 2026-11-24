"""
================================================================
QuantumEdge — Paper Trading Test (пауза без ордерів)
================================================================
Бере ЖИВІ дані з BingX (OHLCV 1h + поточна ціна), проганяє через:
  - tools.sigtest.indicators  (тренд/моментум/волат/об'єм)
  - tools.liqmap.model        (теплова карта ліквідацій → зони-магніти)
Синтезує КОРОТКИЙ торговий план (без виконання ордерів).

Запуск:
  .venv/bin/python3 -m tools.papertest --symbol BTC/USDT:USDT
================================================================
"""
from __future__ import annotations
import sys, os, json, argparse
from pathlib import Path
import numpy as np
import pandas as pd
import ccxt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.sigtest.indicators import enrich
from tools.liqmap.model import build_heatmap, _extract_zones


def load_market(symbol: str, tf: str = "1h", limit: int = 200):
    """Живі дані з BingX (публічні + ключі з .env якщо є)."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass
    ex = ccxt.bingx({"enableRateLimit": True})
    ohlcv = ex.fetch_ohlcv(symbol, tf, limit=limit)
    ticker = ex.fetch_ticker(symbol)
    last = float(ticker["last"])
    return ohlcv, last


def indicator_bias(df: pd.DataFrame) -> dict:
    """Повертає напрям за індикаторами (логіка trend_score з sigtest)."""
    r = df.iloc[-1]
    bull = bear = 0
    # EMA-стрічка
    if r.ema9 > r.ema21 > r.ema50:
        bull += 1
    elif r.ema9 < r.ema21 < r.ema50:
        bear += 1
    # MACD
    if r.macd_hist > 0:
        bull += 1
    else:
        bear += 1
    # RSI
    if r.rsi > 55:
        bull += 1
    elif r.rsi < 45:
        bear += 1
    # BB позиція
    if r.price > r.bb_pos * 0:  # заглушка, використовуємо bb_pos як індикатор
        pass
    score = bull - bear
    bias = "LONG" if score >= 2 else ("SHORT" if score <= -2 else "NEUTRAL")
    return {
        "bull": bull, "bear": bear, "score": score, "bias": bias,
        "rsi": round(float(r.rsi), 1), "macd_hist": round(float(r.macd_hist), 2),
        "adx": round(float(r.adx), 1), "obv_slope": round(float(r.obv_slope), 1),
        "bb_pos": round(float(r.bb_pos), 3) if not np.isnan(r.bb_pos) else None,
        "atr_pct": round(float(r.atr_pct), 2),
    }


def liq_zones(ohlcv, last_price, bins=140):
    """Будує карту ліквідацій, повертає найближчі зони ≤2%."""
    heat = build_heatmap(ohlcv, price_bins=bins)
    grid = np.array(heat["grid"])
    profile = grid[:, -1]                   # поточний профіль (останній час)
    centers = np.array(heat["price_levels"])
    zones = _extract_zones(profile, centers, last_price, top=8)
    near = []
    for z in zones:
        dist = abs(z["dist_pct"])
        if dist <= 2.0:
            near.append({
                "side": z["side"], "price": round(z["price"], 1),
                "dist_pct": round(z["dist_pct"], 2),
                "intensity": round(z.get("intensity", 0), 2),
            })
    return near, zones


def plan(symbol, bias, zones_near, last):
    """Синтезує короткий план (без ордерів)."""
    lines = []
    lines.append(f"=== ПАПЕРОВИЙ ТЕСТ: {symbol} @ {last:.1f} ===")
    lines.append(f"ІНДИКАТОРИ: bias={bias['bias']} (score {bias['score']:+d}, "
                 f"bull {bias['bull']}/bear {bias['bear']})")
    lines.append(f"  RSI={bias['rsi']} MACD_hist={bias['macd_hist']} ADX={bias['adx']} "
                 f"OBV_slope={bias['obv_slope']} BB_pos={bias['bb_pos']} ATR%={bias['atr_pct']}")
    lines.append(f"КАРТА ЛІКВІДАЦІЙ (зони ≤2%): {len(zones_near)} шт")
    for z in zones_near:
        lines.append(f"  {z['side']:9s} @ {z['price']:.1f} ({z['dist_pct']:+.2f}%) "
                     f"intensity={z['intensity']}")
    # Синтез сигналу
    lines.append("--- ПЛАН ТОРГІВ (папір) ---")
    if not zones_near:
        lines.append("  Близьких зон ліквідації немає → НЕ входити (низька впевненість).")
        return "\n".join(lines)
    # близька зона визначає бік магніту (пріоритет — найближча)
    all_near = sorted(zones_near, key=lambda x: abs(x["dist_pct"]))
    primary = all_near[0]
    lines.append(f"  ПРІОРИТЕТ (найближча зона {abs(primary['dist_pct']):.2f}%): "
                 f"{primary['side']} @ {primary['price']:.1f}")
    for z in all_near:
        if z["side"] == "LONG_LIQ":
            lines.append(f"  Магніт ВНИЗ: LONG_LIQ @ {z['price']:.1f} "
                         f"(відстань {z['dist_pct']:+.2f}%) → сетап SHORT до зони.")
            lines.append(f"  Вхід: ринок/лиміт біля {last:.1f}, TP={z['price']:.1f}, "
                         f"SL вище найближчої SHORT_LIQ зони або структурний рівень.")
        else:
            lines.append(f"  Магніт ВГОРУ: SHORT_LIQ @ {z['price']:.1f} "
                         f"(відстань {z['dist_pct']:+.2f}%) → сетап LONG до зони.")
            lines.append(f"  Вхід: ринок/лиміт біля {last:.1f}, TP={z['price']:.1f}, "
                         f"SL нижче найближчої LONG_LIQ зони або структурний рівень.")
    # фільтр індикаторів
    ind_b = bias["bias"]
    if ind_b != "NEUTRAL":
        lines.append(f"  Фільтр індикаторів: {ind_b} (слабкий предиктор, лише як "
                     f"підтвердження таймінгу, НЕ як головний сигнал).")
    lines.append("  ⚠️ Ордери НЕ виконуються. Це лише синтез за скілами.")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTC/USDT:USDT")
    ap.add_argument("--tf", default="1h")
    ap.add_argument("--limit", type=int, default=200)
    args = ap.parse_args()

    ohlcv, last = load_market(args.symbol, args.tf, args.limit)
    df = enrich(pd.DataFrame(ohlcv, columns=["ts", "open", "high", "low", "close", "volume"]))
    # enrich чекає open/high/low/close/volume (okeй), але треба price
    df["price"] = df["close"]

    bias = indicator_bias(df)
    zones_near, _ = liq_zones(ohlcv, last)

    print(plan(args.symbol, bias, zones_near, last))


if __name__ == "__main__":
    main()
