from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List

from model_router.context.cache import ContextCache
from model_router.context.formatter import ContextFormatter, pct_change, realized_vol, utc_now
from model_router.context.models import ContextPackV1
from model_router.context.sql_templates import QuestDBConfig, build_ohlcv_query


class QuestDBReader:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def query_rows(self, sql: str, params: Dict[str, object]) -> List[Dict[str, object]]:
        try:
            import psycopg
        except Exception as exc:  # pragma: no cover - optional
            raise RuntimeError("psycopg is required for QuestDB reader") from exc

        rows: List[Dict[str, object]] = []
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [desc[0] for desc in cur.description]
            for row in cur.fetchall():
                rows.append(dict(zip(cols, row)))
        return rows


@dataclass
class ContextFetcher:
    reader: QuestDBReader
    cache: ContextCache
    formatter: ContextFormatter
    config: QuestDBConfig

    def get_context(self, symbol: str, lookback_m: int, candle_count: int = 5) -> ContextPackV1:
        cached = self.cache.get(symbol, lookback_m)
        if cached:
            return cached

        query = build_ohlcv_query(self.config, lookback_m, candle_count, symbol)
        rows = self.reader.query_rows(query.sql, query.params)

        ohlcv = []
        closes = []
        t0 = utc_now()
        t1 = t0
        for row in rows:
            t = row.get("t")
            o = float(row.get("o", 0) or 0)
            h = float(row.get("h", 0) or 0)
            low = float(row.get("l", 0) or 0)
            c = float(row.get("c", 0) or 0)
            v = float(row.get("v", 0) or 0)
            ohlcv.append([_to_epoch(t), o, h, low, c, v])
            closes.append(c)

        if ohlcv:
            t0 = _iso_from_epoch(ohlcv[-1][0])
            t1 = _iso_from_epoch(ohlcv[0][0])

        chg = None
        if len(closes) >= 2:
            chg = pct_change(closes[-1], closes[0])

        vol = realized_vol(closes)

        pack = ContextPackV1(
            v=1,
            sym=symbol,
            lbm=lookback_m,
            t0=t0,
            t1=t1,
            ohlcv=ohlcv,
            chg=chg,
            vol=vol,
        )
        self.cache.set(symbol, lookback_m, pack)
        return pack


def _to_epoch(ts) -> float:
    if hasattr(ts, "timestamp"):
        return float(ts.timestamp())
    return float(ts) if ts is not None else 0.0


def _iso_from_epoch(epoch: float) -> str:
    import datetime

    return datetime.datetime.fromtimestamp(epoch, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_fetcher_from_env() -> ContextFetcher:
    dsn = os.environ.get("QDB_PG_DSN", "")
    if not dsn:
        raise RuntimeError("QDB_PG_DSN not set")
    cfg = QuestDBConfig(
        trades_table=os.environ.get("QDB_TRADES_TABLE", "trades"),
        ts_col=os.environ.get("QDB_TS_COL", "timestamp"),
        symbol_col=os.environ.get("QDB_SYMBOL_COL", "symbol"),
        price_col=os.environ.get("QDB_PRICE_COL", "price"),
        amount_col=os.environ.get("QDB_AMOUNT_COL", "amount"),
    )
    cache_ttl = float(os.environ.get("QDB_CONTEXT_CACHE_TTL_S", "3"))
    return ContextFetcher(
        reader=QuestDBReader(dsn),
        cache=ContextCache(cache_ttl),
        formatter=ContextFormatter(max_candles=int(os.environ.get("QDB_CONTEXT_MAX_CANDLES", "5"))),
        config=cfg,
    )
