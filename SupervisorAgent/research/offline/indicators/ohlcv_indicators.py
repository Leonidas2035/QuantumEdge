import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int):
    return series.ewm(span=period, adjust=False).mean().iloc[-1]


def rsi(series: pd.Series, period: int = 14):
    delta = series.diff()

    gain = (delta.where(delta > 0, 0)).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()

    rs = gain / (loss + 1e-10)
    rsi = 100 - (100 / (1 + rs))

    return rsi.iloc[-1]


def atr(df: pd.DataFrame, period=14):
    df["hl"] = df["high"] - df["low"]
    df["hc"] = (df["high"] - df["close"].shift()).abs()
    df["lc"] = (df["low"] - df["close"].shift()).abs()

    tr = df[["hl", "hc", "lc"]].max(axis=1)
    return tr.rolling(period).mean().iloc[-1]


def vwap(df: pd.DataFrame):
    pv = (df["close"] * df["volume"]).cumsum()
    vol = df["volume"].cumsum()
    return (pv / vol).iloc[-1]
