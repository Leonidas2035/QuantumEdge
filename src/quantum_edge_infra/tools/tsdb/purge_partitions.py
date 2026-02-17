from __future__ import annotations

import argparse
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

TABLE_GROUPS = {
    "l0_raw": ["market_trades_raw"],
    "l1_bars": ["market_l1", "bars_1s", "bars_1m"],
    "l2_telemetry": [
        "signals",
        "orders",
        "fills",
        "positions",
        "equity",
        "risk_events",
    ],
}


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


def _questdb_exec(query_url: str, sql: str, timeout: float = 5.0) -> None:
    encoded = urllib.parse.quote(sql, safe="")
    url = f"{query_url}?query={encoded}"
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if resp.status >= 300:
            raise RuntimeError(f"QuestDB exec failed: {resp.status}")


def _build_sql(table: str, days: int) -> str:
    return f"ALTER TABLE {table} DROP PARTITION WHERE ts < dateadd('d', -{days}, systimestamp());"


def main() -> None:
    cfg = _load_tsdb_config()
    questdb_cfg = cfg.get("questdb", {}) or {}
    retention_cfg = cfg.get("retention_days", {}) or {}
    default_query_url = questdb_cfg.get("query_url", "http://127.0.0.1:9000/exec")

    parser = argparse.ArgumentParser(
        description="Purge QuestDB partitions based on retention policy."
    )
    parser.add_argument(
        "--apply", action="store_true", help="Apply retention (otherwise dry-run)."
    )
    parser.add_argument(
        "--query-url", default=default_query_url, help="QuestDB /exec URL."
    )
    parser.add_argument("--retention-l0", type=int, help="Override L0 retention days.")
    parser.add_argument("--retention-l1", type=int, help="Override L1 retention days.")
    parser.add_argument("--retention-l2", type=int, help="Override L2 retention days.")
    args = parser.parse_args()

    retention = {
        "l0_raw": int(args.retention_l0 or retention_cfg.get("l0_raw", 14)),
        "l1_bars": int(args.retention_l1 or retention_cfg.get("l1_bars", 180)),
        "l2_telemetry": int(
            args.retention_l2 or retention_cfg.get("l2_telemetry", 180)
        ),
    }

    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    for group, tables in TABLE_GROUPS.items():
        days = retention.get(group, 0)
        if days <= 0:
            continue
        for table in tables:
            sql = _build_sql(table, days)
            if not args.apply:
                print(f"[dry-run] {sql}")
                continue
            _questdb_exec(args.query_url, sql)
            print(
                f"[apply] {table}: dropped partitions older than {days} days (as of {now_iso})"
            )


if __name__ == "__main__":
    main()
