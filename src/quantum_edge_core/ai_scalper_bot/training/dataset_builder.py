"""Dataset Builder — fetch Binance klines and engineer features for XGBoost.

Usage:
    python -m quantum_edge_core.ai_scalper_bot.training.dataset_builder

Output: runtime/dataset.csv
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import List, Optional
from urllib import error, request

import json
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────
BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
DEFAULT_SYMBOL = "BTCUSDT"
DEFAULT_INTERVAL = "1m"
DEFAULT_DAYS = 7
FUTURE_BARS = 5  # look-ahead: 5 minutes
TARGET_THRESHOLD = 0.001  # 0.1% move → signal

# Kline indices
_OPEN_TIME, _OPEN, _HIGH, _LOW, _CLOSE, _VOLUME = 0, 1, 2, 3, 4, 5


# ═══════════════════════════════════════════════════════════════════
# 1. Data Fetching (Binance public REST, no auth)
# ═══════════════════════════════════════════════════════════════════


def _fetch_klines_batch(
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    limit: int = 1000,
    timeout: float = 15.0,
) -> List[list]:
    """Fetch a single batch of klines from Binance."""
    url = (
        f"{BINANCE_KLINES_URL}?"
        f"symbol={symbol}&interval={interval}"
        f"&startTime={start_ms}&endTime={end_ms}&limit={limit}"
    )
    req = request.Request(url, headers={"Accept": "application/json"})
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (error.URLError, json.JSONDecodeError) as exc:
        logger.warning("Kline fetch failed: %s", exc)
        return []


def fetch_klines(
    symbol: str = DEFAULT_SYMBOL,
    interval: str = DEFAULT_INTERVAL,
    days: int = DEFAULT_DAYS,
) -> pd.DataFrame:
    """Fetch N days of 1m klines with pagination (max 1000 per request)."""
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - days * 24 * 60 * 60 * 1000
    all_klines: List[list] = []

    cursor = start_ms
    batch_num = 0
    while cursor < now_ms:
        batch_num += 1
        batch = _fetch_klines_batch(symbol, interval, cursor, now_ms, limit=1000)
        if not batch:
            break
        all_klines.extend(batch)
        # Move cursor past the last received candle
        cursor = int(batch[-1][_OPEN_TIME]) + 60_000  # +1 min
        logger.info(
            "  Batch %d: %d candles (total: %d)", batch_num, len(batch), len(all_klines)
        )
        time.sleep(0.3)  # Rate limit courtesy

    if not all_klines:
        raise RuntimeError(f"No klines fetched for {symbol} {interval}")

    df = pd.DataFrame(all_klines)
    df = df.iloc[:, :6]
    df.columns = ["open_time", "open", "high", "low", "close", "volume"]
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(np.float64)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = (
        df.drop_duplicates(subset="open_time")
        .sort_values("open_time")
        .reset_index(drop=True)
    )
    return df


# ═══════════════════════════════════════════════════════════════════
# 2. Feature Engineering (institutional-grade indicators)
# ═══════════════════════════════════════════════════════════════════


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(window=period, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).rolling(window=period, min_periods=period).mean()
    rs = gain / (loss + 1e-10)
    return 100.0 - (100.0 / (1.0 + rs))


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add institutional trading features to OHLCV DataFrame."""
    c = df["close"]
    h = df["high"]
    l = df["low"]  # noqa: E741
    v = df["volume"]

    # ── Micro-volatility (5-bar rolling std of returns) ───────────
    df["returns_1m"] = c.pct_change()
    df["micro_vol_5"] = df["returns_1m"].rolling(5).std() * 100  # in %

    # ── RSI (14) ─────────────────────────────────────────────────
    df["rsi_14"] = _rsi(c, 14)

    # ── MACD (12, 26, signal 9) ──────────────────────────────────
    ema12 = _ema(c, 12)
    ema26 = _ema(c, 26)
    df["macd"] = ema12 - ema26
    df["macd_signal"] = _ema(df["macd"], 9)
    df["macd_hist"] = df["macd"] - df["macd_signal"]

    # ── VWAP distance (rolling 20-bar VWAP) ──────────────────────
    typical_price = (h + l + c) / 3.0
    cumtp = (typical_price * v).rolling(20).sum()
    cumvol = v.rolling(20).sum()
    vwap = cumtp / (cumvol + 1e-10)
    df["vwap_dist_pct"] = (c - vwap) / (vwap + 1e-10) * 100.0

    # ── Volume Rate of Change (VROC, 10-bar) ─────────────────────
    df["vroc_10"] = v.pct_change(10) * 100.0

    # ── ATR (14) ─────────────────────────────────────────────────
    tr = pd.concat(
        [
            h - l,
            (h - c.shift(1)).abs(),
            (l - c.shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)
    df["atr_14"] = tr.rolling(14).mean()

    # ── Candle body ratio (body / wick → momentum gauge) ─────────
    body = (c - df["open"]).abs()
    wick = h - l
    df["body_ratio"] = body / (wick + 1e-10)

    # ── Volume moving average ratio ──────────────────────────────
    df["vol_ma_ratio"] = v / (v.rolling(20).mean() + 1e-10)

    return df


# ═══════════════════════════════════════════════════════════════════
# 3. Target Engineering (classification: up/down/flat)
# ═══════════════════════════════════════════════════════════════════


def create_target(
    df: pd.DataFrame,
    future_bars: int = FUTURE_BARS,
    threshold: float = TARGET_THRESHOLD,
) -> pd.DataFrame:
    """Create target labels: 1 (up >0.1%), -1 (down >0.1%), 0 (flat)."""
    future_close = df["close"].shift(-future_bars)
    future_return = (future_close - df["close"]) / df["close"]

    df["target"] = 0
    df.loc[future_return > threshold, "target"] = 1
    df.loc[future_return < -threshold, "target"] = -1

    # Also store the raw future return for regression
    df["future_return_5m"] = future_return

    return df


# ═══════════════════════════════════════════════════════════════════
# 4. Pipeline
# ═══════════════════════════════════════════════════════════════════

FEATURE_COLS = [
    "returns_1m",
    "micro_vol_5",
    "rsi_14",
    "macd",
    "macd_signal",
    "macd_hist",
    "vwap_dist_pct",
    "vroc_10",
    "atr_14",
    "body_ratio",
    "vol_ma_ratio",
]


def build_dataset(
    symbol: str = DEFAULT_SYMBOL,
    days: int = DEFAULT_DAYS,
    output_path: str = "runtime/dataset.csv",
) -> pd.DataFrame:
    """Full pipeline: fetch → features → labels → save CSV."""
    logger.info("═" * 60)
    logger.info("Dataset Builder — %s, %d days, interval=1m", symbol, days)
    logger.info("═" * 60)

    # 1. Fetch
    logger.info("[1/4] Fetching klines from Binance...")
    df = fetch_klines(symbol, days=days)
    logger.info("  Raw candles: %d", len(df))

    # 2. Features
    logger.info("[2/4] Engineering features...")
    df = engineer_features(df)

    # 3. Labels
    logger.info(
        "[3/4] Creating target labels (future=%d bars, threshold=%.2f%%)...",
        FUTURE_BARS,
        TARGET_THRESHOLD * 100,
    )
    df = create_target(df)

    # 4. Clean NaN rows (from rolling windows + future shift)
    initial_len = len(df)
    df = df.dropna(subset=FEATURE_COLS + ["target"]).reset_index(drop=True)
    logger.info(
        "  Dropped %d rows with NaN → %d clean rows", initial_len - len(df), len(df)
    )

    # Class balance
    counts = df["target"].value_counts().to_dict()
    logger.info(
        "  Class balance: UP=%d, FLAT=%d, DOWN=%d",
        counts.get(1, 0),
        counts.get(0, 0),
        counts.get(-1, 0),
    )

    # 5. Save
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    logger.info("[4/4] Saved dataset → %s (%.1f MB)", out, out.stat().st_size / 1e6)

    return df


# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    build_dataset()
