"""Warm Start (Historical Backfill) module for MarketDataHub.

Fetches historical 1m klines from Binance REST API and injects them
into QuestDB via ILP to ensure the TSDB has warm-path data before
the live real-time streams begin.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
import aiohttp

from quantum_edge_core.market_data.tsdb.quest_writer import QuestILPWriter

logger = logging.getLogger("WarmStart")


async def run_warm_start(symbol: str, writer: Optional[QuestILPWriter]) -> None:
    """Fetch historical klines and write them to TSDB.

    Parameters
    ----------
    symbol : str
        The trading pair, e.g., "BTCUSDT".
    writer : QuestILPWriter
        The ILP writer connected to QuestDB.
    """
    if not writer:
        logger.info("No QuestILPWriter configured. Skipping Warm Start.")
        return

    logger.info("Starting Warm Start: Fetching historical klines for %s", symbol)
    url = "https://fapi.binance.com/fapi/v1/klines"
    params = {
        "symbol": symbol,
        "interval": "1m",
        "limit": 100,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=10.0) as response:
                if response.status >= 300:
                    text = await response.text()
                    logger.error(
                        "Warm Start failed to fetch klines: %s %s",
                        response.status,
                        text,
                    )
                    return
                data = await response.json()
    except Exception as exc:
        logger.error("Warm Start HTTP request failed: %s", exc)
        return

    if not data:
        logger.warning("Warm Start received empty data from Binance.")
        return

    points = 0
    for row in data:
        if len(row) < 6:
            continue
        try:
            # Timestamp from binance is in ms, ILP expects ns
            ts_ms = int(row[0])
            ts_ns = ts_ms * 1_000_000

            # Use 'kline' table name to match real-time
            table = "kline"
            symbols = {
                "symbol": symbol,
                "interval": "1m",
            }
            columns = {
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
                "trades": int(row[8]) if len(row) > 8 else 0,
            }
            writer.enqueue(table, symbols, columns, ts_ns)
            points += 1
        except (ValueError, TypeError) as exc:
            logger.warning("Warm Start failed to parse row: %s", exc)
            continue

    if points:
        logger.info("Warm Start completed. Historical data injected. (%d rows)", points)
    else:
        logger.warning("Warm Start found no valid rows to inject.")
