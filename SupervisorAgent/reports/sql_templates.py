from __future__ import annotations


def equity_curve_sql(start_iso: str, bucket: str) -> str:
    return (
        "SELECT bot_id, ts, avg(equity) as equity "
        "FROM equity WHERE ts >= '"
        + start_iso
        + "' SAMPLE BY "
        + bucket
        + " ALIGN TO CALENDAR"
    )


def pnl_per_symbol_sql(start_iso: str) -> str:
    return (
        "SELECT o.symbol, "
        "sum(case when o.side='SELL' then f.price * f.qty else -f.price * f.qty end) as pnl_gross "
        "FROM fills f JOIN orders o ON f.client_order_id = o.client_order_id AND f.symbol = o.symbol "
        "WHERE f.ts >= '"
        + start_iso
        + "' GROUP BY o.symbol"
    )


def order_counts_sql(start_iso: str, bucket: str) -> str:
    return (
        "SELECT bot_id, symbol, count() as orders "
        "FROM orders WHERE ts >= '"
        + start_iso
        + "' SAMPLE BY "
        + bucket
        + " ALIGN TO CALENDAR"
    )


def fill_counts_sql(start_iso: str, bucket: str) -> str:
    return (
        "SELECT bot_id, symbol, count() as fills "
        "FROM fills WHERE ts >= '"
        + start_iso
        + "' SAMPLE BY "
        + bucket
        + " ALIGN TO CALENDAR"
    )


def risk_events_counts_sql(start_iso: str) -> str:
    return (
        "SELECT bot_id, symbol, level, count() as events "
        "FROM risk_events WHERE ts >= '"
        + start_iso
        + "' GROUP BY bot_id, symbol, level"
    )


def latency_stats_sql(start_iso: str, bucket: str) -> str:
    return (
        "SELECT symbol, ts, "
        "avg(inference_p50_ms) as inference_p50_ms, "
        "avg(inference_p95_ms) as inference_p95_ms "
        "FROM qe_metrics WHERE ts >= '"
        + start_iso
        + "' SAMPLE BY "
        + bucket
        + " ALIGN TO CALENDAR"
    )


def asof_fill_l1_sql(start_iso: str) -> str:
    return (
        "SELECT f.symbol, f.ts, f.price, l1.bid, l1.ask "
        "FROM fills f ASOF JOIN market_l1 l1 "
        "ON f.symbol = l1.symbol "
        "WHERE f.ts >= '"
        + start_iso
        + "'"
    )
