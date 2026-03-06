"""MTF Resampler — Queries QuestDB REST API for multi-timeframe candle data.

Uses ``aiohttp`` to call QuestDB ``/exec`` endpoint and computes
linear regression slopes (via numpy) for each timeframe.

The output is a ``MarketContext`` object ready for consumption by
the XGBoost Hot Path and LLM Cold Path.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger("Resampler")

# Lazy import — aiohttp may not be installed in test env
try:
    import aiohttp
except ImportError:
    aiohttp = None  # type: ignore[assignment]

from quantum_edge_core.market_data.market_context import (
    MarketContext,
    TimeframeSlope,
)


class Resampler:
    """Multi-timeframe resampler powered by QuestDB materialized views.

    Parameters
    ----------
    questdb_url : str
        QuestDB REST endpoint, e.g. ``http://127.0.0.1:9000``.
    symbol : str
        Trading pair to query.
    timeframes : list[str]
        Intervals to query, e.g. ``["5m", "15m", "1h", "4h"]``.
    lookback_candles : int
        Number of recent candles per timeframe for slope calculation.
    """

    TIMEFRAMES_DEFAULT = ["5m", "15m", "1h", "4h"]

    def __init__(
        self,
        questdb_url: str = "http://127.0.0.1:9000",
        symbol: str = "BTCUSDT",
        timeframes: Optional[List[str]] = None,
        lookback_candles: int = 20,
    ):
        self.questdb_url = questdb_url.rstrip("/")
        self.symbol = symbol
        self.timeframes = timeframes or self.TIMEFRAMES_DEFAULT
        self.lookback_candles = lookback_candles

    async def fetch_mtf(
        self,
        wall_features: Optional[Dict[str, float]] = None,
        current_price: float = 0.0,
    ) -> MarketContext:
        """Fetch MTF slopes from QuestDB and build a MarketContext.

        Parameters
        ----------
        wall_features : dict, optional
            Output of OrderBookAggregator.process_book().
        current_price : float
            Latest market price.

        Returns
        -------
        MarketContext
        """
        slopes: Dict[str, TimeframeSlope] = {}

        for tf in self.timeframes:
            table = f"klines_{tf}"
            try:
                candles = await self._query_candles(table, self.symbol)
                if candles:
                    slope_info = self._compute_slope(candles, tf)
                    slopes[tf] = slope_info
            except Exception as exc:
                logger.warning("Failed to fetch %s: %s", table, exc)

        return MarketContext(
            symbol=self.symbol,
            current_price=current_price,
            slopes=slopes,
            walls=wall_features or {},
            timestamp=time.time(),
        )

    async def _query_candles(self, table: str, symbol: str) -> List[Dict[str, Any]]:
        """Query QuestDB REST /exec for recent candle data."""
        sql = (
            f"SELECT open, high, low, close, volume, ts "
            f"FROM {table} "
            f"WHERE symbol = '{symbol}' "
            f"ORDER BY ts DESC "
            f"LIMIT {self.lookback_candles}"
        )

        if aiohttp is None:
            logger.error("aiohttp not installed — cannot query QuestDB")
            return []

        url = f"{self.questdb_url}/exec"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, params={"query": sql}, timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status != 200:
                        logger.warning("QuestDB query failed: HTTP %d", resp.status)
                        return []
                    data = await resp.json()
                    return self._parse_questdb_response(data)
        except Exception as exc:
            logger.warning("QuestDB REST error: %s", exc)
            return []

    @staticmethod
    def _parse_questdb_response(data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Parse QuestDB JSON response into list of row dicts."""
        columns = [c["name"] for c in data.get("columns", [])]
        rows = data.get("dataset", [])
        return [dict(zip(columns, row)) for row in rows]

    @staticmethod
    def _compute_slope(candles: List[Dict[str, Any]], interval: str) -> TimeframeSlope:
        """Compute linear regression slope on close prices.

        Uses numpy polyfit(degree=1) for simple linear regression.
        Returns slope and R² (goodness of fit).
        """
        closes = [float(c.get("close", 0.0)) for c in reversed(candles)]
        n = len(closes)
        if n < 2:
            return TimeframeSlope(
                interval=interval, slope=0.0, r_squared=0.0, candle_count=n
            )

        x = np.arange(n, dtype=np.float64)
        y = np.array(closes, dtype=np.float64)

        # Normalize y for numerical stability
        y_mean = np.mean(y)
        if y_mean > 0:
            y_norm = y / y_mean
        else:
            y_norm = y

        coeffs = np.polyfit(x, y_norm, 1)
        slope = float(coeffs[0])

        # R² calculation
        y_pred = np.polyval(coeffs, x)
        ss_res = np.sum((y_norm - y_pred) ** 2)
        ss_tot = np.sum((y_norm - np.mean(y_norm)) ** 2)
        r_squared = float(1.0 - ss_res / ss_tot) if ss_tot > 0 else 0.0

        return TimeframeSlope(
            interval=interval,
            slope=round(slope, 6),
            r_squared=round(max(0.0, min(1.0, r_squared)), 4),
            candle_count=n,
        )
