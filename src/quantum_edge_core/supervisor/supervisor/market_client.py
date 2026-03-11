"""Multi-timeframe OHLCV market data client for Situation Analysis.

Fetches candlestick data from Binance public REST API and compresses
it into a token-efficient text summary for the LLM prompt.

Usage (sync, for standalone / LLM Supervisor):

    from quantum_edge_core.supervisor.supervisor.market_client import (
        fetch_situation_summary,
        mock_situation_summary,
    )

    # Live (hits Binance public API — no auth required)
    summary_text = fetch_situation_summary("BTCUSDT")

    # Demo / offline
    summary_text = mock_situation_summary()
"""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple
from urllib import error, request

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────

BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
DEFAULT_SYMBOL = "BTCUSDT"
DEFAULT_TIMEFRAMES = ("5m", "1h", "4h")
DEFAULT_LIMIT = 20

# Kline array indices (Binance REST response)
_OPEN_TIME = 0
_OPEN = 1
_HIGH = 2
_LOW = 3
_CLOSE = 4
_VOLUME = 5
_CLOSE_TIME = 6


# ── Raw Data Fetching ────────────────────────────────────────────────


def _fetch_klines(
    symbol: str,
    interval: str,
    limit: int = DEFAULT_LIMIT,
    timeout: float = 10.0,
) -> List[list]:
    """Fetch raw klines from Binance public API (no auth required)."""

    url = f"{BINANCE_KLINES_URL}?symbol={symbol}&interval={interval}&limit={limit}"
    req = request.Request(url, headers={"Accept": "application/json"})

    try:
        with request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body)
    except error.URLError as exc:
        logger.warning("Failed to fetch %s %s klines: %s", symbol, interval, exc)
        return []
    except json.JSONDecodeError as exc:
        logger.warning("Invalid kline JSON for %s %s: %s", symbol, interval, exc)
        return []


def fetch_multi_timeframe(
    symbol: str = DEFAULT_SYMBOL,
    timeframes: Tuple[str, ...] = DEFAULT_TIMEFRAMES,
    limit: int = DEFAULT_LIMIT,
    timeout: float = 10.0,
) -> Dict[str, List[list]]:
    """Fetch klines for multiple timeframes in parallel (ThreadPool)."""

    results: Dict[str, List[list]] = {}

    with ThreadPoolExecutor(max_workers=len(timeframes)) as pool:
        futures = {
            pool.submit(_fetch_klines, symbol, tf, limit, timeout): tf
            for tf in timeframes
        }
        for future in as_completed(futures):
            tf = futures[future]
            try:
                results[tf] = future.result()
            except Exception as exc:
                logger.warning("Kline fetch failed for %s: %s", tf, exc)
                results[tf] = []

    return results


# ── Token-Optimized Summary ──────────────────────────────────────────


def _trend_direction(klines: List[list]) -> str:
    """Determine overall trend from N candles: UP, DOWN, or FLAT."""

    if len(klines) < 2:
        return "UNKNOWN"

    first_open = float(klines[0][_OPEN])
    last_close = float(klines[-1][_CLOSE])

    if first_open == 0:
        return "UNKNOWN"

    change_pct = (last_close - first_open) / first_open * 100

    if change_pct > 0.3:
        return "UP"
    elif change_pct < -0.3:
        return "DOWN"
    return "FLAT"


def _summarize_timeframe(tf: str, klines: List[list]) -> str:
    """Compress klines into a 1-line summary for the LLM prompt.

    Format: TF: O=X H=X L=X C=X | Vol=X | Trend=UP/DOWN/FLAT | Chg=+X.XX%
    """

    if not klines:
        return f"{tf}: NO DATA"

    # Use the last CLOSED candle (second-to-last if the list includes current)
    # Binance returns the current (incomplete) candle as the last element.
    closed = klines[-2] if len(klines) > 1 else klines[-1]

    o = float(closed[_OPEN])
    h = float(closed[_HIGH])
    low_price = float(closed[_LOW])
    c = float(closed[_CLOSE])
    vol = float(closed[_VOLUME])

    # Aggregate stats across all candles
    total_vol = sum(float(k[_VOLUME]) for k in klines)
    high_of_range = max(float(k[_HIGH]) for k in klines)
    low_of_range = min(float(k[_LOW]) for k in klines)

    trend = _trend_direction(klines)

    first_open = float(klines[0][_OPEN])
    last_close = float(klines[-1][_CLOSE])
    change_pct = ((last_close - first_open) / first_open * 100) if first_open else 0

    return (
        f"{tf}: O={o:.1f} H={h:.1f} L={low_price:.1f} C={c:.1f} | "
        f"Vol={total_vol:.1f} | Range=[{low_of_range:.1f}-{high_of_range:.1f}] | "
        f"Trend={trend} Chg={change_pct:+.2f}%"
    )


def format_situation_block(
    multi_tf_data: Dict[str, List[list]],
    symbol: str = DEFAULT_SYMBOL,
) -> str:
    """Build the SITUATION ANALYSIS text block for the LLM prompt.

    Output order: 4h → 1h → 5m (macro → micro).
    """

    lines = [f"SITUATION ANALYSIS ({symbol}):"]

    # Order: longest timeframe first (top-down)
    tf_order = ["4h", "1h", "5m"]
    for tf in tf_order:
        klines = multi_tf_data.get(tf, [])
        lines.append(f"  {_summarize_timeframe(tf, klines)}")

    return "\n".join(lines)


# ── Public API ───────────────────────────────────────────────────────


def fetch_situation_summary(
    symbol: str = DEFAULT_SYMBOL,
    timeframes: Tuple[str, ...] = DEFAULT_TIMEFRAMES,
    timeout: float = 10.0,
) -> str:
    """Fetch live OHLCV from Binance and return formatted summary text."""

    t0 = time.monotonic()
    data = fetch_multi_timeframe(symbol, timeframes, timeout=timeout)
    elapsed = (time.monotonic() - t0) * 1000
    logger.info("Fetched %d timeframes in %.0f ms", len(data), elapsed)
    return format_situation_block(data, symbol)


def mock_situation_summary(symbol: str = DEFAULT_SYMBOL) -> str:
    """Return hardcoded OHLCV summary for demo/test mode (no network)."""

    return (
        f"SITUATION ANALYSIS ({symbol}):\n"
        f"  4h: O=96500.0 H=97200.0 L=96100.0 C=96800.0 | "
        f"Vol=4200.5 | Range=[95800.0-97500.0] | Trend=UP Chg=+0.85%\n"
        f"  1h: O=96700.0 H=97000.0 L=96600.0 C=96850.0 | "
        f"Vol=1100.2 | Range=[96400.0-97100.0] | Trend=FLAT Chg=+0.12%\n"
        f"  5m: O=96820.0 H=96900.0 L=96780.0 C=96850.0 | "
        f"Vol=280.8 | Range=[96750.0-96920.0] | Trend=FLAT Chg=+0.03%"
    )


def get_current_price(
    symbol: str = DEFAULT_SYMBOL,
    timeout: float = 10.0,
) -> float:
    """Fetch the latest price for the symbol from Binance public API."""
    url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
    req = request.Request(url, headers={"Accept": "application/json"})

    try:
        with request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            data = json.loads(body)
            return float(data["price"])
    except Exception as exc:
        logger.warning("Failed to fetch %s current price: %s", symbol, exc)
        return 96000.0  # Fallback rough value or you can throw
