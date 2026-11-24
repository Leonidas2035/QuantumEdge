"""
================================================================
QuantumEdge — SIGNAL BACKTEST: розширені індикатори
================================================================
Обчислює НАБІР індикаторів як pandas-серій (не лише останнє значення),
щоб бектест-движок міг рахувати сигнал у КОЖНІЙ точці історії.

Осі (незалежні категорії):
  TREND    : EMA9/21/50 стрічка, ADX
  MOMENTUM : MACD (hist), RSI14
  VOLAT    : Bollinger width/pos, ATR%
  VOLUME   : OBV нахил
================================================================
"""
from __future__ import annotations
import pandas as pd
import numpy as np
import ta


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    """Додає колонки індикаторів до OHLCV df. Повертає новий df."""
    d = df.copy()
    c, h, l, v = d["close"].astype(float), d["high"].astype(float), d["low"].astype(float), d["volume"].astype(float)

    # TREND — EMA стрічка
    d["ema9"] = ta.trend.EMAIndicator(c, 9).ema_indicator()
    d["ema21"] = ta.trend.EMAIndicator(c, 21).ema_indicator()
    d["ema50"] = ta.trend.EMAIndicator(c, 50).ema_indicator()
    # ADX (сила тренду)
    adx = ta.trend.ADXIndicator(h, l, c, 14)
    d["adx"] = adx.adx()
    d["di_plus"] = adx.adx_pos()
    d["di_minus"] = adx.adx_neg()

    # MOMENTUM
    macd = ta.trend.MACD(c)
    d["macd_hist"] = macd.macd_diff()
    d["rsi"] = ta.momentum.RSIIndicator(c, 14).rsi()

    # VOLATILITY
    bb = ta.volatility.BollingerBands(c, 20, 2)
    up, lo, mid = bb.bollinger_hband(), bb.bollinger_lband(), bb.bollinger_mavg()
    d["bb_pos"] = (c - lo) / (up - lo).replace(0, np.nan)
    d["bb_width"] = (up - lo) / mid.replace(0, np.nan)
    d["atr_pct"] = ta.volatility.AverageTrueRange(h, l, c, 14).average_true_range() / c * 100

    # VOLUME — OBV та його нахил (10 барів)
    d["obv"] = ta.volume.OnBalanceVolumeIndicator(c, v).on_balance_volume()
    d["obv_slope"] = d["obv"].diff(10)

    return d
