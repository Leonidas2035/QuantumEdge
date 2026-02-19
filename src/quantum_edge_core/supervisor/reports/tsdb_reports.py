from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from reports.sql_templates import (equity_curve_sql, fill_counts_sql,
                                   latency_stats_sql, order_counts_sql,
                                   pnl_per_symbol_sql, risk_events_counts_sql)
from tsdb.questdb_client import QuestDbClient

_DURATION_RE = re.compile(r"^(?P<value>\\d+)(?P<unit>[smhd])$")


def _parse_duration(value: Optional[str]) -> timedelta:
    if not value:
        return timedelta(hours=24)
    match = _DURATION_RE.match(value.strip())
    if not match:
        return timedelta(hours=24)
    qty = int(match.group("value"))
    unit = match.group("unit")
    if unit == "s":
        return timedelta(seconds=qty)
    if unit == "m":
        return timedelta(minutes=qty)
    if unit == "h":
        return timedelta(hours=qty)
    return timedelta(days=qty)


def _default_bucket(window: timedelta) -> str:
    seconds = int(window.total_seconds())
    if seconds <= 6 * 3600:
        return "1m"
    if seconds <= 2 * 86400:
        return "5m"
    return "1h"


def _safe_query(client: QuestDbClient, sql: str) -> Dict[str, Any]:
    try:
        return {"rows": client.query(sql)}
    except Exception as exc:
        return {"error": str(exc), "sql": sql}


def build_report(
    client: QuestDbClient, last: Optional[str] = None, bucket: Optional[str] = None
) -> Dict[str, Any]:
    window = _parse_duration(last)
    bucket_value = bucket or _default_bucket(window)
    end_ts = datetime.now(timezone.utc)
    start_ts = end_ts - window
    start_iso = start_ts.isoformat().replace("+00:00", "Z")

    report = {
        "window": {
            "last": last or "24h",
            "start": start_iso,
            "end": end_ts.isoformat().replace("+00:00", "Z"),
            "bucket": bucket_value,
        },
        "equity_curve": _safe_query(client, equity_curve_sql(start_iso, bucket_value)),
        "pnl_per_symbol": _safe_query(client, pnl_per_symbol_sql(start_iso)),
        "order_counts": _safe_query(client, order_counts_sql(start_iso, bucket_value)),
        "fill_counts": _safe_query(client, fill_counts_sql(start_iso, bucket_value)),
        "risk_events": _safe_query(client, risk_events_counts_sql(start_iso)),
        "latency_stats": _safe_query(
            client, latency_stats_sql(start_iso, bucket_value)
        ),
    }
    return report
