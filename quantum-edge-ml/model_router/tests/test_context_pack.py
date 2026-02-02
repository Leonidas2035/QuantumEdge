from __future__ import annotations

import ast

import pytest

from model_router.context.cache import ContextCache
from model_router.context.formatter import ContextFormatter
from model_router.context.models import ContextPackV1
from model_router.context.sql_templates import QuestDBConfig, build_ohlcv_query


def test_sql_rendering_safe():
    cfg = QuestDBConfig(
        trades_table="trades",
        ts_col="timestamp",
        symbol_col="symbol",
        price_col="price",
        amount_col="amount",
    )
    query = build_ohlcv_query(cfg, lookback_m=10, candle_count=5, symbol="BTC")
    assert "FROM trades" in query.sql
    assert "%(sym)s" in query.sql
    assert query.params["sym"] == "BTC"


@pytest.mark.parametrize("bad", ["trades;drop", "price-1", "123bad"])
def test_sql_rendering_rejects_invalid(bad):
    cfg = QuestDBConfig(
        trades_table=bad,
        ts_col="timestamp",
        symbol_col="symbol",
        price_col="price",
        amount_col="amount",
    )
    with pytest.raises(ValueError):
        build_ohlcv_query(cfg, lookback_m=10, candle_count=5, symbol="BTC")


def test_formatter_bounds_candles():
    pack = ContextPackV1(
        v=1,
        sym="BTCUSDT",
        lbm=15,
        t0="2025-01-01T00:00:00Z",
        t1="2025-01-01T00:15:00Z",
        ohlcv=[[i, 1, 2, 3, 4, 5] for i in range(10)],
        chg=0.1,
        vol=0.2,
    )
    formatter = ContextFormatter(max_candles=5)
    line = formatter.format(pack)
    cndl_part = line.split("cndl=")[-1]
    candles = ast.literal_eval(cndl_part)
    assert len(candles) == 5


def test_context_cache_ttl(monkeypatch):
    cache = ContextCache(ttl_s=1)
    cache.set("BTC", 15, "value")
    assert cache.get("BTC", 15) == "value"

    import model_router.context.cache as cache_module

    original_time = cache_module.time.time
    monkeypatch.setattr(cache_module.time, "time", lambda: original_time() + 5)
    assert cache.get("BTC", 15) is None
