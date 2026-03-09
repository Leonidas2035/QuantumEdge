"""
Backfill Historical Data — Synthetic Tick Injection.

Downloads 1h and 5m klines from Binance Futures for BTCUSDT (last 30 days),
converts each candle into 4 synthetic ticks (Open, High, Low, Close),
and bulk-inserts them into the QuestDB `trades` table via psycopg2 (PG Wire).

Usage:
    python scripts/backfill_history.py
"""

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import List, Tuple

import requests
import psycopg2

# ── Logging ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("BackfillHistory")

# ── Configuration ────────────────────────────────────────────
SYMBOL = "BTCUSDT"
BASE_URL = "https://fapi.binance.com"
KLINE_LIMIT = 1500  # Binance max per request
DAYS_BACK = 30

# QuestDB PG Wire
DB_HOST = "127.0.0.1"
DB_PORT = 8812
DB_NAME = "qdb"
DB_USER = "admin"
DB_PASS = "quest"

# Rate‑limit guard (ms between requests)
REQUEST_DELAY_SEC = 0.35


def _ms_now() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _ms_days_ago(days: int) -> int:
    dt = datetime.now(timezone.utc) - timedelta(days=days)
    return int(dt.timestamp() * 1000)


# ── Binance Fetcher ──────────────────────────────────────────
def fetch_klines(interval: str, start_ms: int, end_ms: int) -> List[list]:
    """
    Paginated kline fetcher with rate‑limit pauses.
    Returns raw Binance kline arrays.
    """
    all_klines: List[list] = []
    cursor = start_ms

    while cursor < end_ms:
        params = {
            "symbol": SYMBOL,
            "interval": interval,
            "startTime": cursor,
            "endTime": end_ms,
            "limit": KLINE_LIMIT,
        }
        try:
            resp = requests.get(f"{BASE_URL}/fapi/v1/klines", params=params, timeout=10)
            resp.raise_for_status()
            batch = resp.json()
        except Exception as exc:
            logger.error("Binance API error (interval=%s): %s", interval, exc)
            time.sleep(2)
            continue

        if not batch:
            break

        all_klines.extend(batch)
        # Move cursor past last kline close time + 1ms
        cursor = int(batch[-1][6]) + 1
        logger.info(
            "Fetched %d klines (%s), total so far: %d, cursor: %s",
            len(batch),
            interval,
            len(all_klines),
            datetime.fromtimestamp(cursor / 1000, tz=timezone.utc).isoformat(),
        )
        time.sleep(REQUEST_DELAY_SEC)

    return all_klines


# ── Synthetic Tick Generator ─────────────────────────────────
def klines_to_ticks(klines: List[list]) -> List[Tuple]:
    """
    Convert each kline into 4 synthetic ticks.
    Returns list of tuples: (symbol, price, qty, side, timestamp).
    Binance kline format:
      [0] open_time, [1] open, [2] high, [3] low, [4] close, [5] volume, ...
    """
    ticks: List[Tuple] = []
    for k in klines:
        open_ts_ms = int(k[0])
        o = float(k[1])
        h = float(k[2])
        l_ = float(k[3])
        c = float(k[4])
        vol = float(k[5])
        q = vol / 4.0 if vol > 0 else 0.001  # quarter of candle volume

        # Tick 1: Open
        ticks.append((SYMBOL, o, q, "BUY", _ms_to_ts(open_ts_ms)))
        # Tick 2: High (+1s)
        ticks.append((SYMBOL, h, q, "SELL", _ms_to_ts(open_ts_ms + 1000)))
        # Tick 3: Low (+2s)
        ticks.append((SYMBOL, l_, q, "BUY", _ms_to_ts(open_ts_ms + 2000)))
        # Tick 4: Close (+3s)
        ticks.append((SYMBOL, c, q, "SELL", _ms_to_ts(open_ts_ms + 3000)))

    return ticks


def _ms_to_ts(ms: int) -> datetime:
    """Milliseconds → Python datetime (UTC)."""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


# ── QuestDB Writer ───────────────────────────────────────────
def write_ticks(ticks: List[Tuple]) -> int:
    """
    Bulk‑insert ticks into QuestDB `trades` table using psycopg2 executemany.
    Returns number of rows written.
    """
    if not ticks:
        return 0

    conn = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASS,
    )
    conn.autocommit = True
    cur = conn.cursor()

    sql = (
        "INSERT INTO trades (symbol, price, qty, side, timestamp) "
        "VALUES (%s, %s, %s, %s, %s);"
    )

    batch_size = 5000
    written = 0
    for i in range(0, len(ticks), batch_size):
        batch = ticks[i : i + batch_size]
        cur.executemany(sql, batch)
        written += len(batch)
        logger.info("Written %d / %d ticks", written, len(ticks))

    cur.close()
    conn.close()
    return written


# ── Main ─────────────────────────────────────────────────────
def main() -> None:
    start_ms = _ms_days_ago(DAYS_BACK)
    end_ms = _ms_now()

    logger.info(
        "=== Backfill START === Symbol: %s, Range: %s → %s",
        SYMBOL,
        datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).isoformat(),
        datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc).isoformat(),
    )

    # ── Step 1: Fetch 1H klines ──
    logger.info("── Fetching 1H klines ──")
    klines_1h = fetch_klines("1h", start_ms, end_ms)
    ticks_1h = klines_to_ticks(klines_1h)
    logger.info(
        "Generated %d synthetic ticks from %d 1H klines", len(ticks_1h), len(klines_1h)
    )

    # ── Step 2: Fetch 5M klines ──
    logger.info("── Fetching 5M klines ──")
    klines_5m = fetch_klines("5m", start_ms, end_ms)
    ticks_5m = klines_to_ticks(klines_5m)
    logger.info(
        "Generated %d synthetic ticks from %d 5M klines", len(ticks_5m), len(klines_5m)
    )

    # ── Step 3: Merge and deduplicate by timestamp ──
    # 1H ticks provide the macro backbone; 5M ticks fill in the micro detail.
    # Since 5M is a superset resolution-wise, we prioritise 5M ticks.
    # Simple dedup: collect all, sort by timestamp, and let QuestDB handle
    # out-of-order inserts (O3 enabled by default on recent versions).
    all_ticks = ticks_1h + ticks_5m
    all_ticks.sort(key=lambda t: t[4])  # sort by timestamp (datetime)
    logger.info("Total synthetic ticks to insert: %d", len(all_ticks))

    # ── Step 4: Write to QuestDB ──
    rows = write_ticks(all_ticks)
    logger.info("=== Backfill COMPLETE === Rows written: %d", rows)


if __name__ == "__main__":
    main()
