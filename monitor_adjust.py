#!/home/korben/QuantumEdge-main/venv/bin/python3
"""
Cron job: monitor_adjust.py
Analyze BTCUSDT 15m chart, compute TA, fetch positions from BingX VST,
decide action plan, attempt policy update, log outcome.
"""
import asyncio
import datetime
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import ccxt
import numpy as np
import pandas as pd
import ta

PROJECT_ROOT = Path("/home/korben/QuantumEdge-main")
LOG_FILE = PROJECT_ROOT / "logs" / "monitor_adjust.log"
CURRENCY = "BTC/USDT:USDT"
INTERVAL = "15m"
HTTP_POLICY_URL = "http://127.0.0.1:5559/api/v1/policy/update"


def setup_logging():
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(LOG_FILE),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    return logging.getLogger("monitor_adjust")


def fetch_ohlcv():
    exchange = ccxt.bingx({
        "apiKey": "6Knpgh8fw7mXbXU1mMZGURawzmwkQnyhZQSWN14vhgVAwsmAYXx63hb4ETrSbS4mbFqqRCqY2TNWfbhw1modA",
        "secret": "9HNdQw3utfQTGa09PCtf2NU7ILQcMO04VTPsE3lyGcvQGeQwOWXYOBL5cNG2OYVpW4H2DWW2j4lU2nOjA",
        "enableRateLimit": True,
        "options": {"defaultType": "swap"},
    })
    exchange.set_sandbox_mode(True)
    raw = exchange.fetch_ohlcv(CURRENCY, INTERVAL, limit=100)
    df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["close"] = df["close"].astype(float)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)
    return df


def compute_indicators(df: pd.DataFrame):
    close = df["close"]
    high = df["high"]
    low = df["low"]

    rsi = ta.momentum.RSIIndicator(close, window=14).rsi().iloc[-1]

    macd = ta.trend.MACD(close)
    macd_line = macd.macd().iloc[-1]
    macd_signal = macd.macd_signal().iloc[-1]
    macd_hist = macd.macd_diff().iloc[-1]

    bb = ta.volatility.BollingerBands(close, window=20, window_dev=2)
    bb_lower = bb.bollinger_lband().iloc[-1]
    bb_mid = bb.bollinger_mavg().iloc[-1]
    bb_upper = bb.bollinger_hband().iloc[-1]

    atr = ta.volatility.AverageTrueRange(high, low, close, window=14).average_true_range().iloc[-1]

    price = close.iloc[-1]
    return {
        "price": float(price),
        "rsi_14": float(rsi),
        "macd_line": float(macd_line),
        "macd_signal": float(macd_signal),
        "macd_hist": float(macd_hist),
        "bb_lower": float(bb_lower),
        "bb_mid": float(bb_mid),
        "bb_upper": float(bb_upper),
        "atr_14": float(atr),
    }


def fetch_positions():
    exchange = ccxt.bingx({
        "apiKey": "6Knpgh8fw7mXbXU1mMZGURawzmwkQnyhZQSWN14vhgVAwsmAYXx63hb4ETrSbS4mbFqqRCqY2TNWfbhw1modA",
        "secret": "9HNdQw3utfQTGa09PCtf2NU7ILQcMO04VTPsE3lyGcvQGeQwOWXYOBL5cNG2OYVpW4H2DWW2j4lU2nOjA",
        "enableRateLimit": True,
        "options": {"defaultType": "swap"},
    })
    exchange.set_sandbox_mode(True)
    positions = exchange.fetch_positions(symbols=[CURRENCY])
    buy_positions = []
    sell_positions = []
    for p in positions:
        size = float(p.get("contracts") or p.get("size") or 0.0)
        if size <= 0:
            continue
        entry = float(p.get("entryPrice") or p.get("averagePrice") or 0.0)
        pnl = float(p.get("unrealizedPnl") or 0.0)
        lev = float(p.get("leverage") or 1.0)
        info = {
            "symbol": p.get("symbol"),
            "side": p.get("side", "").upper(),
            "size": size,
            "entry_price": entry,
            "unrealized_pnl": pnl,
            "leverage": lev,
        }
        if info["side"] == "LONG":
            buy_positions.append(info)
        elif info["side"] == "SHORT":
            sell_positions.append(info)
    return buy_positions, sell_positions


def decide_action_plan(indicators: dict, buy_positions: list, sell_positions: list):
    """
    Decide action plan: price_rise (bullish) or price_decline (bearish).
    Uses RSI, MACD, and Bollinger Band position as proxy for prior plan logic.
    """
    price = indicators["price"]
    rsi = indicators["rsi_14"]
    macd = indicators["macd_line"]
    signal = indicators["macd_signal"]
    bb_lower = indicators["bb_lower"]
    bb_upper = indicators["bb_upper"]
    bb_mid = indicators["bb_mid"]

    bullish_score = 0
    bearish_score = 0

    if rsi > 55:
        bullish_score += 1
    elif rsi < 45:
        bearish_score += 1

    if macd > signal:
        bullish_score += 1
    elif macd < signal:
        bearish_score += 1

    if price > bb_mid:
        bullish_score += 1
    elif price < bb_mid:
        bearish_score += 1

    if price <= bb_lower:
        bearish_score += 1
    if price >= bb_upper:
        bullish_score += 1

    plan = "price_rise" if bullish_score >= bearish_score else "price_decline"

    if plan == "price_rise":
        params = {
            "risk_multiplier": 1.5,
            "grid_spacing": 0.002,
            "qty": 0.001,
            "stop_loss": 0.02,
            "trailing_tp": 0.02,
            "max_position_size": 0.05,
        }
    else:
        params = {
            "risk_multiplier": 0.5,
            "grid_spacing": 0.001,
            "qty": 0.0005,
            "stop_loss": 0.01,
            "trailing_tp": 0.005,
            "max_position_size": 0.01,
        }

    return plan, params


def build_payload(plan: str, params: dict):
    return {
        "plan": plan,
        "risk_multiplier": params["risk_multiplier"],
        "grid_spacing": params["grid_spacing"],
        "qty": params["qty"],
        "stop_loss": params["stop_loss"],
        "trailing_tp": params["trailing_tp"],
        "max_position_size": params["max_position_size"],
    }


def attempt_policy_update(payload: dict):
    payload_path = PROJECT_ROOT / "payload.json"
    payload_path.write_text(json.dumps(payload, indent=2))

    # Step 5: send via curl
    try:
        result = subprocess.run(
            [
                "curl",
                "-s",
                "-o", "/tmp/policy_response.json",
                "-w", "%{http_code}",
                "-X", "POST",
                HTTP_POLICY_URL,
                "-H", "Content-Type: application/json",
                "-d", str(payload_path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        http_status = result.stdout.strip()
        response_body = ""
        resp_path = Path("/tmp/policy_response.json")
        if resp_path.exists():
            response_body = resp_path.read_text()
        return int(http_status) if http_status.isdigit() else -1, response_body, ""
    except Exception as e:
        return -1, "", str(e)


def tail_bot_log(lines=30):
    log_path = PROJECT_ROOT / "bot.log"
    if not log_path.exists():
        return ""
    try:
        with open(log_path, "r", errors="replace") as f:
            all_lines = f.readlines()
        return "".join(all_lines[-lines:])
    except Exception:
        return ""


def main():
    logger = setup_logging()
    logger.info("=== monitor_adjust run start ===")

    try:
        df = fetch_ohlcv()
    except Exception as e:
        logger.error("Failed to fetch OHLCV: %s", e)
        sys.exit(1)

    indicators = compute_indicators(df)
    logger.info("Indicators: %s", json.dumps(indicators, indent=2))

    try:
        buy_positions, sell_positions = fetch_positions()
    except Exception as e:
        logger.error("Failed to fetch positions: %s", e)
        buy_positions, sell_positions = [], []

    logger.info("BUY positions: %s", json.dumps(buy_positions, indent=2))
    logger.info("SELL positions: %s", json.dumps(sell_positions, indent=2))

    plan, params = decide_action_plan(indicators, buy_positions, sell_positions)
    payload = build_payload(plan, params)
    logger.info("Selected plan: %s", plan)
    logger.info("Payload: %s", json.dumps(payload, indent=2))

    http_status, response_body, error = attempt_policy_update(payload)
    if error:
        logger.error("Policy update failed with error: %s", error)
    else:
        logger.info("Policy update HTTP status: %s", http_status)
        logger.info("Policy update response: %s", response_body)

    # Verify bot respect by checking recent bot.log
    recent_log = tail_bot_log(30)
    if recent_log:
        logger.info("Tail bot.log (last 30 lines):\n%s", recent_log)
    else:
        logger.info("bot.log not found or empty.")

    logger.info("=== monitor_adjust run end ===")


if __name__ == "__main__":
    main()
