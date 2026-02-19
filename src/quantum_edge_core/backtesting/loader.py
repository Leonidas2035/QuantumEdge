"""
QuestDB Data Loader for Backtesting.
Fetches historical data via HTTP API.
"""

import csv
import io
import logging
from datetime import datetime
from typing import Any, Dict, Iterator, List

import requests

logger = logging.getLogger(__name__)


class QuestDataLoader:
    """
    Loads trade and liquidation data from QuestDB.
    """

    def __init__(self, host: str = "http://localhost:9000"):
        self.base_url = f"{host}/exec"

    def _query(self, query: str) -> List[Dict[str, Any]]:
        """
        Executes SQL query and returns list of dicts.
        """
        try:
            params = {"query": query, "fmt": "csv"}
            resp = requests.get(self.base_url, params=params)
            resp.raise_for_status()

            # Parse CSV directly
            reader = csv.DictReader(io.StringIO(resp.text))
            return list(reader)
        except Exception as e:
            logger.error(f"QuestDB Query Failed: {e}")
            return []

    def load_data(
        self, symbol: str, start_time: datetime, end_time: datetime
    ) -> Iterator[Dict[str, Any]]:
        """
        Loads trades and liquidations, merges them, sorts by time,
        and yields event dictionaries.
        """
        # Format timestamps for QuestDB (ISO format usually works or 'yyyy-MM-ddTHH:mm:ss.SSSZ')
        # QuestDB uses 'IN' for time range or standard SQL comparison if timestamp column is indexed

        t_start = start_time.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        t_end = end_time.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

        # 1. Fetch Trades
        query_trades = f"""
        SELECT timestamp, price, amount as quantity, side, 'trade' as type
        FROM trades
        WHERE symbol = '{symbol}'
        AND timestamp BETWEEN '{t_start}' AND '{t_end}'
        """
        logger.info(f"Fetching trades for {symbol}...")
        trades = self._query(query_trades)

        # 2. Fetch Liquidations
        query_liqs = f"""
        SELECT timestamp, price, qty as quantity, side, 'liquidation' as type
        FROM liquidations
        WHERE symbol = '{symbol}'
        AND timestamp BETWEEN '{t_start}' AND '{t_end}'
        """
        logger.info(f"Fetching liquidations for {symbol}...")
        liqs = self._query(query_liqs)

        # 3. Merge
        events = trades + liqs
        if not events:
            logger.warning("No data found.")
            return

        # 4. Sort and parse timestamps
        # QuestDB timestamps are ISO strings usually: '2025-01-01T00:00:00.000000Z'
        def parse_ts(x):
            try:
                # Attempt standard ISO parsing or strptime
                # Python 3.7+ fromisoformat handles some ISO, but Z might be tricky depending on version
                return datetime.fromisoformat(x["timestamp"].replace("Z", "+00:00"))
            except ValueError:
                # Fallback format if needed
                return datetime.strptime(x["timestamp"], "%Y-%m-%dT%H:%M:%S.%fZ")

        # Parse timestamp for ALL events
        for e in events:
            if isinstance(e["timestamp"], str):
                e["timestamp"] = parse_ts(e)

        events.sort(key=lambda x: x["timestamp"])

        logger.info(f"Loaded {len(events)} events.")

        for e in events:
            yield e
