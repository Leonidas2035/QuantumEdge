"""
src/quantum_edge_core/market_data/backfiller.py

Historical data backfiller for warm-starting the QuantumEdge HFT system.
Downloads kline (candlestick) data from Binance Futures REST API
and writes it to QuestDB via ILP to pre-populate indicators.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from typing import Any, Dict, List

import aiohttp
import structlog

from quantum_edge_core.logging_setup import setup_logging
from quantum_edge_core.market_data.tsdb.quest_writer import QuestILPWriter

logger = structlog.get_logger()

BINANCE_FUTURES_KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"


async def fetch_historical_klines(
    session: aiohttp.ClientSession, symbol: str, interval: str, limit: int
) -> List[List[Any]]:
    """Fetch klines from Binance Futures REST API."""
    params: Dict[str, Any] = {
        "symbol": symbol.upper(),
        "interval": interval,
        "limit": limit,
    }

    logger.info(
        "Fetching historical data from Binance",
        symbol=symbol,
        interval=interval,
        limit=limit,
        url=BINANCE_FUTURES_KLINES_URL,
    )

    async with session.get(BINANCE_FUTURES_KLINES_URL, params=params) as response:
        if response.status != 200:
            text = await response.text()
            logger.error(
                "Failed to fetch data from Binance",
                status_code=response.status,
                response_text=text,
            )
            response.raise_for_status()

        data = await response.json()
        logger.info(
            "Successfully fetched klines",
            symbol=symbol,
            count=len(data),
        )
        return data


async def backfill_klines(
    symbol: str, interval: str, limit: int, host: str = "127.0.0.1", port: int = 9009
) -> None:
    """Fetch and write klines to QuestDB."""
    async with aiohttp.ClientSession() as session:
        klines = await fetch_historical_klines(session, symbol, interval, limit)

    if not klines:
        logger.warning("No klines returned from Binance. Exiting.")
        return

    writer = QuestILPWriter(host=host, port=port)
    await writer.connect()

    logger.info(
        "Enqueuing historical klines to QuestDB", count=len(klines), symbol=symbol
    )

    for k in klines:
        # Binance Kline REST API Response Format:
        # [
        #   [
        #     1499040000000,      // Kline open time
        #     "0.01634790",       // Open price
        #     "0.80000000",       // High price
        #     "0.01575800",       // Low price
        #     "0.01577100",       // Close price
        #     "148976.11427815",  // Volume
        #     1499644799999,      // Kline Close time
        #     "2434.19055334",    // Quote asset volume
        #     308,                // Number of trades
        #     "1756.87402397",    // Taker buy base asset volume
        #     "28.46694368",      // Taker buy quote asset volume
        #     "0"                 // Unused field, ignore.
        #   ]
        # ]

        open_time_ms = int(k[0])
        open_price = float(k[1])
        high_price = float(k[2])
        low_price = float(k[3])
        close_price = float(k[4])
        volume = float(k[5])
        trades_count = int(k[8])

        # Convert millisecond timestamp to nanoseconds
        timestamp_ns = open_time_ms * 1_000_000

        # Note: In `hub.py`, KlineEvent timestamp is written to `klines_1m` table using open time.
        writer.enqueue(
            table="klines_1m",
            symbols={"symbol": symbol},
            columns={
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
                "volume": volume,
                "trades_count": trades_count,
            },
            timestamp_ns=timestamp_ns,
        )

    # Let the writer process the queue
    logger.info("Waiting for QuestDB writer to finish enqueueing...")
    # Small sleep to allow background task to pick up
    await asyncio.sleep(2)
    await writer.stop()
    logger.info("Backfill complete.", symbol=symbol, interval=interval, limit=limit)


def main() -> None:
    """CLI Entry point for backfiller."""
    setup_logging()
    parser = argparse.ArgumentParser(
        description="Warm-start backfiller for QuantumEdge HFT."
    )
    parser.add_argument(
        "--symbol",
        type=str,
        default="BTCUSDT",
        help="Trading symbol to backfill (default: BTCUSDT).",
    )
    parser.add_argument(
        "--interval",
        type=str,
        default="1m",
        help="Kline interval (default: 1m).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=1440,
        help="Number of klines to fetch. 1440 = 24h for 1m (default: 1440).",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="QuestDB ILP host (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=9009,
        help="QuestDB ILP port (default: 9009).",
    )

    args = parser.parse_args()

    asyncio.run(
        backfill_klines(
            symbol=args.symbol,
            interval=args.interval,
            limit=args.limit,
            host=args.host,
            port=args.port,
        )
    )


if __name__ == "__main__":
    main()
