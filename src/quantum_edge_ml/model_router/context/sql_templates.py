from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict

IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass
class QuestDBConfig:
    trades_table: str
    ts_col: str
    symbol_col: str
    price_col: str
    amount_col: str


@dataclass
class QuerySpec:
    sql: str
    params: Dict[str, object]


def _safe_ident(name: str) -> str:
    if not IDENT_RE.match(name):
        raise ValueError(f"Invalid identifier: {name}")
    return name


def build_ohlcv_query(
    cfg: QuestDBConfig, lookback_m: int, candle_count: int, symbol: str
) -> QuerySpec:
    table = _safe_ident(cfg.trades_table)
    ts = _safe_ident(cfg.ts_col)
    sym = _safe_ident(cfg.symbol_col)
    price = _safe_ident(cfg.price_col)
    amount = _safe_ident(cfg.amount_col)

    sql = (
        f"SELECT {ts} AS t, "
        f"first({price}) AS o, "
        f"max({price}) AS h, "
        f"min({price}) AS l, "
        f"last({price}) AS c, "
        f"sum({amount}) AS v "
        f"FROM {table} "
        f"WHERE {sym} = %(sym)s AND {ts} >= dateadd('m', -%(lbm)s, now()) "
        "SAMPLE BY 1m ALIGN TO CALENDAR "
        "ORDER BY t DESC "
        "LIMIT %(limit)s"
    )
    return QuerySpec(
        sql=sql, params={"sym": symbol, "lbm": lookback_m, "limit": candle_count}
    )
