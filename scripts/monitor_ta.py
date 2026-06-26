#!/usr/bin/env python3
"""BTCUSDT 15m Technical Analysis Script for QuantumEdge monitor."""

import json
import sys
import time
from datetime import datetime, timezone
from urllib import request as urllib_request

import pandas as pd
import ta

SYMBOL = "BTCUSDT"
INTERVAL = "15m"
LIMIT = 100
BINANCE_KLINES = "https://api.binance.com/api/v3/klines"


def fetch_klines(symbol, interval, limit=100):
    url = f"{BINANCE_KLINES}?symbol={symbol}&interval={interval}&limit={limit}"
    req = urllib_request.Request(url, headers={"Accept": "application/json"})
    with urllib_request.urlopen(req, timeout=15) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body)


def compute_indicators(klines):
    # Binance kline format: [open_time, open, high, low, close, volume, close_time, ...]
    df = pd.DataFrame(klines, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "count", "taker_buy_volume",
        "taker_buy_quote_volume", "ignore"
    ])
    df["open"] = df["open"].astype(float)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)
    df["close"] = df["close"].astype(float)
    df["volume"] = df["volume"].astype(float)

    # RSI(14)
    df["rsi"] = ta.momentum.RSIIndicator(df["close"], window=14).rsi()
    # MACD(12,26,9)
    macd = ta.trend.MACD(df["close"], window_fast=12, window_slow=26, window_sign=9)
    df["macd_line"] = macd.macd()
    df["macd_signal"] = macd.macd_signal()
    df["macd_hist"] = macd.macd_diff()
    # Bollinger Bands(20,2)
    bb = ta.volatility.BollingerBands(df["close"], window=20, window_dev=2)
    df["bb_upper"] = bb.bollinger_hband()
    df["bb_middle"] = bb.bollinger_mavg()
    df["bb_lower"] = bb.bollinger_lband()
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_middle"]
    # ATR(14)
    df["atr"] = ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"], window=14).average_true_range()

    latest = df.iloc[-1]
    return {
        "timestamp": int(latest["open_time"]),
        "datetime_utc": datetime.fromtimestamp(latest["open_time"] / 1000, tz=timezone.utc).isoformat(),
        "price": latest["close"],
        "rsi": round(float(latest["rsi"]), 4) if not pd.isna(latest["rsi"]) else None,
        "macd_line": round(float(latest["macd_line"]), 4) if not pd.isna(latest["macd_line"]) else None,
        "macd_signal": round(float(latest["macd_signal"]), 4) if not pd.isna(latest["macd_signal"]) else None,
        "macd_hist": round(float(latest["macd_hist"]), 4) if not pd.isna(latest["macd_hist"]) else None,
        "bb_upper": round(float(latest["bb_upper"]), 2) if not pd.isna(latest["bb_upper"]) else None,
        "bb_middle": round(float(latest["bb_middle"]), 2) if not pd.isna(latest["bb_middle"]) else None,
        "bb_lower": round(float(latest["bb_lower"]), 2) if not pd.isna(latest["bb_lower"]) else None,
        "bb_width_pct": round(float(latest["bb_width"]) * 100, 4) if not pd.isna(latest["bb_width"]) else None,
        "atr": round(float(latest["atr"]), 2) if not pd.isna(latest["atr"]) else None,
    }


def fetch_dashboard_positions():
    url = "http://127.0.0.1:8765/api/v1/dashboard/status"
    req = urllib_request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib_request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            ai = data.get("ai_scalper", {})
            dyndca = data.get("dyndca", {})
            return {
                "ai_scalper": {
                    "last_price": ai.get("last_price"),
                    "active_signal": ai.get("active_signal"),
                    "atr": ai.get("atr"),
                },
                "dyndca": {
                    "active_positions_count": dyndca.get("metrics", {}).get("active_positions_count", 0),
                    "position_size": dyndca.get("metrics", {}).get("position_size", 0),
                    "average_entry_price": dyndca.get("metrics", {}).get("average_entry_price"),
                }
            }
    except Exception as e:
        return {"error": str(e)}


def main():
    try:
        klines = fetch_klines(SYMBOL, INTERVAL, LIMIT)
        indicators = compute_indicators(klines)
        positions = fetch_dashboard_positions()
        result = {
            "symbol": SYMBOL,
            "timeframe": INTERVAL,
            "indicators": indicators,
            "positions": positions,
        }
        print(json.dumps(result, indent=2))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
