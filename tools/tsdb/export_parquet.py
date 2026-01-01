from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


def _load_tsdb_config() -> Dict[str, Any]:
    path = Path(__file__).resolve().parents[2] / "config" / "tsdb.yaml"
    if not path.exists():
        return {}
    try:
        import yaml
    except Exception:
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _query_questdb(query_url: str, sql: str, timeout: float = 5.0) -> List[Dict[str, Any]]:
    encoded = urllib.parse.quote(sql, safe="")
    url = f"{query_url}?query={encoded}&fmt=json"
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        payload = json.loads(resp.read().decode("utf-8"))
    return _rows_from_questdb(payload)


def _rows_from_questdb(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    cols = [col.get("name") for col in payload.get("columns", [])]
    rows = []
    for row in payload.get("dataset", []) or []:
        rows.append({cols[i]: row[i] if i < len(row) else None for i in range(len(cols))})
    return rows


def _parse_dt(value: str) -> datetime:
    if "T" in value:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc)
    dt = datetime.fromisoformat(value)
    return dt.replace(tzinfo=timezone.utc)


def _iter_ranges(start: datetime, end: datetime, granularity: str) -> Iterable[tuple[datetime, datetime]]:
    cursor = start
    while cursor < end:
        if granularity == "hour":
            nxt = cursor + timedelta(hours=1)
        else:
            nxt = cursor + timedelta(days=1)
        yield cursor, min(nxt, end)
        cursor = nxt


def _ensure_parquet_writer() -> Any:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except Exception as exc:
        raise SystemExit("pyarrow is required for Parquet export. Install with: pip install pyarrow") from exc
    return pa, pq


def _write_parquet(rows: List[Dict[str, Any]], out_path: Path) -> None:
    pa, pq = _ensure_parquet_writer()
    table = pa.Table.from_pylist(rows)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, out_path)


def _build_output_path(base: Path, table: str, symbol: Optional[str], window_start: datetime, granularity: str) -> Path:
    date_part = window_start.strftime("%Y-%m-%d")
    if granularity == "hour":
        date_part = f"{date_part}/{window_start.strftime('%H')}"
    symbol_part = symbol or "all"
    return base / table / symbol_part / date_part / "export.parquet"


def _build_sql(table: str, start: datetime, end: datetime, symbol: Optional[str], bot_id: Optional[str]) -> str:
    start_iso = start.isoformat().replace("+00:00", "Z")
    end_iso = end.isoformat().replace("+00:00", "Z")
    where = [f"ts >= '{start_iso}'", f"ts < '{end_iso}'"]
    if symbol:
        where.append(f"symbol = '{symbol}'")
    if bot_id:
        where.append(f"bot_id = '{bot_id}'")
    return f"SELECT * FROM {table} WHERE " + " AND ".join(where)


def main() -> None:
    cfg = _load_tsdb_config()
    questdb_cfg = cfg.get("questdb", {}) or {}
    default_query_url = questdb_cfg.get("query_url", "http://127.0.0.1:9000/exec")

    parser = argparse.ArgumentParser(description="Export QuestDB tables to Parquet.")
    parser.add_argument("--table", action="append", required=True, help="Table name (repeatable).")
    parser.add_argument("--symbol", help="Optional symbol filter.")
    parser.add_argument("--bot-id", help="Optional bot_id filter.")
    parser.add_argument("--from", dest="from_ts", required=True, help="Start date or ISO timestamp.")
    parser.add_argument("--to", dest="to_ts", required=True, help="End date or ISO timestamp (exclusive).")
    parser.add_argument("--granularity", choices=["day", "hour"], default="day", help="Partition granularity.")
    parser.add_argument("--query-url", default=default_query_url, help="QuestDB /exec URL.")
    parser.add_argument("--out-dir", default="archive/parquet", help="Output base directory.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing exports.")
    args = parser.parse_args()

    start = _parse_dt(args.from_ts)
    end = _parse_dt(args.to_ts)
    out_base = Path(args.out_dir)
    for table in args.table:
        for window_start, window_end in _iter_ranges(start, end, args.granularity):
            out_path = _build_output_path(out_base, table, args.symbol, window_start, args.granularity)
            if out_path.exists() and not args.overwrite:
                print(f"[export] Skip existing {out_path}")
                continue
            sql = _build_sql(table, window_start, window_end, args.symbol, args.bot_id)
            rows = _query_questdb(args.query_url, sql)
            if not rows:
                print(f"[export] No rows for {table} {window_start.isoformat()} -> {window_end.isoformat()}")
                continue
            _write_parquet(rows, out_path)
            print(f"[export] Wrote {len(rows)} rows to {out_path}")


if __name__ == "__main__":
    main()
