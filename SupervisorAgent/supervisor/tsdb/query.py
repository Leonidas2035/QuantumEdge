"""Query helpers for TSDB backends (QuestDB focus)."""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, List, Optional


ALLOWED_METRICS = {
    "tick_age_ms",
    "book_age_ms",
    "inference_p50_ms",
    "inference_p95_ms",
    "position_notional",
}

ALLOWED_BUCKETS = {"1s", "5s", "10s", "30s", "1m", "5m", "15m", "1h"}


def sanitize_symbol(symbol: str) -> str:
    if not symbol:
        return "unknown"
    if not re.fullmatch(r"[A-Za-z0-9_\\-]+", symbol):
        raise ValueError("invalid_symbol")
    return symbol


def build_timeseries_query(metric: str, symbol: str, start: str, end: str, bucket: str) -> str:
    if metric not in ALLOWED_METRICS:
        raise ValueError("metric_not_allowed")
    if bucket not in ALLOWED_BUCKETS:
        raise ValueError("bucket_not_allowed")
    symbol = sanitize_symbol(symbol)
    start_q = _quote_ts(start)
    end_q = _quote_ts(end)
    return (
        "SELECT timestamp, avg("
        + metric
        + ") as value FROM qe_metrics WHERE symbol='"
        + symbol
        + "' AND timestamp BETWEEN "
        + start_q
        + " AND "
        + end_q
        + " SAMPLE BY "
        + bucket
        + " ALIGN TO CALENDAR"
    )


def questdb_query(query_url: str, sql: str, timeout: float = 3.0) -> List[Dict[str, Any]]:
    encoded = urllib.parse.quote(sql, safe="")
    url = f"{query_url}?query={encoded}&fmt=json"
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        payload = json.loads(resp.read().decode("utf-8"))
    return _rows_from_questdb(payload)


def questdb_exec(query_url: str, sql: str, timeout: float = 3.0) -> None:
    encoded = urllib.parse.quote(sql, safe="")
    url = f"{query_url}?query={encoded}"
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        if resp.status >= 300:
            raise RuntimeError(f"QuestDB exec failed: {resp.status}")


def _rows_from_questdb(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    cols = [col.get("name") for col in payload.get("columns", [])]
    rows = []
    for row in payload.get("dataset", []) or []:
        rows.append({cols[i]: row[i] if i < len(row) else None for i in range(len(cols))})
    return rows


def derive_questdb_query_url(ilp_url: str) -> str:
    if not ilp_url:
        return ""
    if ilp_url.endswith("/imp"):
        return ilp_url[: -len("/imp")] + "/exec"
    if ilp_url.endswith("/imp/"):
        return ilp_url[: -len("/imp/")] + "/exec"
    return ilp_url.rstrip("/") + "/exec"


def _quote_ts(value: str) -> str:
    if value.endswith("Z"):
        return f"'{value}'"
    if value.startswith("'") and value.endswith("'"):
        return value
    return f"'{value}'"
