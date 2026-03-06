#!/usr/bin/env python3
"""QuestDB Schema Setup — Creates tables and materialized views.

Run:
    python scripts/setup_questdb.py [--host 127.0.0.1] [--port 9000]

Creates:
  - klines_1m          (base table, ILP ingested)
  - orderbook_snapshots (base table, ILP ingested)
  - klines_5m / 15m / 1h / 4h  (materialized views)

Idempotent: uses IF NOT EXISTS where possible.
"""

import argparse
import sys
import time
import urllib.parse
import urllib.request
import json


def quest_exec(host: str, port: int, sql: str) -> dict:
    """Execute a SQL statement via QuestDB REST /exec."""
    url = f"http://{host}:{port}/exec"
    params = urllib.parse.urlencode({"query": sql}).encode("utf-8")
    req = urllib.request.Request(url, data=params, method="GET")
    req = urllib.request.Request(f"{url}?{params.decode()}", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        return {"error": body, "status": exc.code}
    except Exception as exc:
        return {"error": str(exc)}


# ═══════════════════════════════════════════════════════════════════════════════
# Table DDL
# ═══════════════════════════════════════════════════════════════════════════════

TABLES = [
    # --- klines_1m (base table ingested via ILP) ---
    """
    CREATE TABLE IF NOT EXISTS klines_1m (
        symbol       SYMBOL        CAPACITY 32 CACHE,
        open         DOUBLE,
        high         DOUBLE,
        low          DOUBLE,
        close        DOUBLE,
        volume       DOUBLE,
        trades_count LONG,
        ts           TIMESTAMP
    ) TIMESTAMP(ts) PARTITION BY DAY WAL
    DEDUP UPSERT KEYS(symbol, ts);
    """,

    # --- orderbook_snapshots (L2 depth snapshots) ---
    """
    CREATE TABLE IF NOT EXISTS orderbook_snapshots (
        symbol              SYMBOL   CAPACITY 32 CACHE,
        side                SYMBOL   CAPACITY 4 CACHE,
        price               DOUBLE,
        qty                 DOUBLE,
        wall_size            DOUBLE,
        wall_distance_pct    DOUBLE,
        depth_level          INT,
        ts                   TIMESTAMP
    ) TIMESTAMP(ts) PARTITION BY DAY WAL;
    """,
]


# ═══════════════════════════════════════════════════════════════════════════════
# Materialized Views (MTF continuous aggregation)
# ═══════════════════════════════════════════════════════════════════════════════

MTF_VIEWS = {
    "klines_5m": "5m",
    "klines_15m": "15m",
    "klines_1h": "1h",
    "klines_4h": "4h",
}


def build_mat_view_sql(view_name: str, interval: str) -> str:
    return f"""
    CREATE MATERIALIZED VIEW IF NOT EXISTS {view_name} AS (
        SELECT
            symbol,
            first(open) AS open,
            max(high)   AS high,
            min(low)    AS low,
            last(close) AS close,
            sum(volume) AS volume,
            sum(trades_count) AS trades_count,
            timestamp_floor('{interval}', ts) AS ts
        FROM klines_1m
        SAMPLE BY {interval} ALIGN TO CALENDAR
    );
    """


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="QuestDB schema setup")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9000)
    args = parser.parse_args()

    print(f"QuestDB Schema Setup — {args.host}:{args.port}")
    print("=" * 60)

    errors = 0

    # 1. Create base tables
    for ddl in TABLES:
        name = ddl.strip().split("\n")[0].strip()
        print(f"\n>>> {name}")
        result = quest_exec(args.host, args.port, ddl.strip())
        if "error" in result:
            print(f"  ❌ ERROR: {result['error']}")
            errors += 1
        else:
            print("  ✅ OK")

    # 2. Create materialized views
    for view_name, interval in MTF_VIEWS.items():
        print(f"\n>>> CREATE MATERIALIZED VIEW {view_name} (SAMPLE BY {interval})")
        sql = build_mat_view_sql(view_name, interval)
        result = quest_exec(args.host, args.port, sql.strip())
        if "error" in result:
            print(f"  ❌ ERROR: {result['error']}")
            errors += 1
        else:
            print("  ✅ OK")

    print("\n" + "=" * 60)
    if errors == 0:
        print("🎉 All schemas created successfully")
    else:
        print(f"⚠️  {errors} error(s) — QuestDB may not be running or version < 8.x")
    return errors


if __name__ == "__main__":
    sys.exit(main())
