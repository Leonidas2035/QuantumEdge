"""
================================================================
QuantumEdge — SIGNAL BACKTEST ENGINE
================================================================
Питання: коли індикатор X дає сигнал (LONG/SHORT), куди йде ціна через
horizon годин і як часто вгадує напрям (win-rate)? Це реальний edge.

Метод: для кожної точки t (після warmup) обчислюємо сигнал кожного
правила, дивимось forward-return = (close[t+h]-close[t])/close[t].
Win = напрям сигналу співпав зі знаком forward-return.
Рахуємо по кожному правилу: n, win_rate, avg_return, edge.

Правила (кожне -> +1 LONG / -1 SHORT / 0 нейтрально):
  ema_ribbon : ema9>ema21>ema50 -> LONG; < -> SHORT
  adx_trend  : ADX>25 і DI+>DI- -> LONG; ADX>25 і DI->DI+ -> SHORT
  macd       : hist>0 -> LONG; <0 -> SHORT
  rsi_rev    : RSI<30 -> LONG(відскок); RSI>70 -> SHORT
  rsi_trend  : RSI>55 -> LONG; RSI<45 -> SHORT
  bb_rev     : bb_pos<0 -> LONG; >1 -> SHORT (повернення в канал)
  bb_break   : bb_pos>1 -> LONG; <0 -> SHORT (продовження пробою)
  obv        : obv_slope>0 -> LONG; <0 -> SHORT
Композит: сума голосів усіх осей; тестуємо пороги.
================================================================
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def _rules(row) -> dict:
    """Повертає {rule_name: +1/-1/0} для одного бару."""
    r = {}
    # TREND
    if not np.isnan(row.ema50):
        if row.ema9 > row.ema21 > row.ema50:
            r["ema_ribbon"] = 1
        elif row.ema9 < row.ema21 < row.ema50:
            r["ema_ribbon"] = -1
        else:
            r["ema_ribbon"] = 0
    if not np.isnan(row.adx):
        if row.adx > 25 and row.di_plus > row.di_minus:
            r["adx_trend"] = 1
        elif row.adx > 25 and row.di_minus > row.di_plus:
            r["adx_trend"] = -1
        else:
            r["adx_trend"] = 0
    # MOMENTUM
    if not np.isnan(row.macd_hist):
        r["macd"] = 1 if row.macd_hist > 0 else -1
    if not np.isnan(row.rsi):
        r["rsi_rev"] = 1 if row.rsi < 30 else (-1 if row.rsi > 70 else 0)
        r["rsi_trend"] = 1 if row.rsi > 55 else (-1 if row.rsi < 45 else 0)
    # VOLATILITY
    if not np.isnan(row.bb_pos):
        r["bb_rev"] = 1 if row.bb_pos < 0 else (-1 if row.bb_pos > 1 else 0)
        r["bb_break"] = 1 if row.bb_pos > 1 else (-1 if row.bb_pos < 0 else 0)
    # VOLUME
    if not np.isnan(row.obv_slope):
        r["obv"] = 1 if row.obv_slope > 0 else -1
    return r


def backtest_signals(df, horizon=24, warmup=60, composite_thresholds=(2, 3, 4)):
    """
    df: enriched OHLCV (з indicators.enrich).
    horizon: годин уперед для forward-return.
    Повертає dict: per_rule stats + composite stats.
    """
    close = df["close"].astype(float).values
    n = len(df)
    rows = list(df.itertuples(index=False))

    rule_stats = {}      # rule -> {"n","wins","ret_sum","long_n","short_n"}
    comp_stats = {}      # threshold -> stats

    for t in range(warmup, n - horizon):
        fwd = (close[t + horizon] - close[t]) / close[t]
        sig = _rules(rows[t])

        # композитний голос
        votes = sum(v for v in sig.values())

        for rule, val in sig.items():
            if val == 0:
                continue
            s = rule_stats.setdefault(rule, {"n": 0, "wins": 0, "ret": 0.0, "long": 0, "short": 0})
            s["n"] += 1
            s["long" if val > 0 else "short"] += 1
            # win = напрям сигналу співпав зі знаком forward-return
            if (val > 0 and fwd > 0) or (val < 0 and fwd < 0):
                s["wins"] += 1
            s["ret"] += fwd * (1 if val > 0 else -1)  # напрям-скоригований дохід

        for th in composite_thresholds:
            direction = 1 if votes >= th else (-1 if votes <= -th else 0)
            if direction == 0:
                continue
            cs = comp_stats.setdefault(th, {"n": 0, "wins": 0, "ret": 0.0, "long": 0, "short": 0})
            cs["n"] += 1
            cs["long" if direction > 0 else "short"] += 1
            if (direction > 0 and fwd > 0) or (direction < 0 and fwd < 0):
                cs["wins"] += 1
            cs["ret"] += fwd * direction

    def fmt(d):
        out = []
        for k, s in sorted(d.items(), key=lambda x: -(x[1]["wins"] / x[1]["n"] if x[1]["n"] else 0)):
            if s["n"] == 0:
                continue
            out.append({
                "signal": k,
                "n": s["n"],
                "win_rate": round(s["wins"] / s["n"], 3),
                "avg_dir_return_pct": round(s["ret"] / s["n"] * 100, 3),
                "long_n": s["long"], "short_n": s["short"],
            })
        return out

    return {"horizon": horizon, "per_rule": fmt(rule_stats), "composite": fmt(comp_stats)}
