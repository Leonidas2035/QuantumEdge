#!/usr/bin/env python3
"""
Probe: QuestDB connectivity & basic table health check.

Validates:
  1. TCP connectivity to ILP port 9009 (used by QuestILPWriter).
  2. HTTP REST API at port 9000 (/exec?query=...).
  3. Postgres Wire at port 8812 (optional — requires psycopg2).

Usage:
    python3 scripts/probe_questdb.py
"""

from __future__ import annotations

import http.client
import json
import logging
import socket
import sys
import urllib.parse

logger = logging.getLogger(__name__)

HOST: str = "127.0.0.1"
ILP_PORT: int = 9009
HTTP_PORT: int = 9000
PG_PORT: int = 8812

# Tables that the system creates via ILP
EXPECTED_TABLES: list[str] = [
    "trades",
    "klines_1m",
    "orderbook_snapshots",
    "liquidations",
]


def check_tcp(host: str, port: int, label: str, timeout: float = 3.0) -> bool:
    """Return True if TCP handshake succeeds."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            logger.info("✅  %s (:%d) — reachable", label, port)
            return True
    except (OSError, ConnectionRefusedError) as exc:
        logger.error("❌  %s (:%d) — %s", label, port, exc)
        return False


def check_http_tables() -> bool:
    """Query QuestDB REST for existing tables."""
    query: str = urllib.parse.quote("SHOW TABLES;")
    try:
        conn = http.client.HTTPConnection(HOST, HTTP_PORT, timeout=5)
        conn.request("GET", f"/exec?query={query}")
        resp = conn.getresponse()
        if resp.status != 200:
            logger.error("❌  QuestDB HTTP returned %d", resp.status)
            return False

        data = json.loads(resp.read())
        tables: list[str] = [row[0] for row in data.get("dataset", [])]
        logger.info("QuestDB tables found: %s", tables)

        missing: list[str] = [t for t in EXPECTED_TABLES if t not in tables]
        if missing:
            logger.warning(
                "⚠️  Missing tables (will be auto-created on first ILP write): %s",
                missing,
            )
        else:
            logger.info("✅  All expected tables present")
        return True
    except Exception as exc:
        logger.error("❌  QuestDB HTTP query failed: %s", exc)
        return False


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    ok: bool = True
    ok &= check_tcp(HOST, ILP_PORT, "QuestDB ILP")
    ok &= check_tcp(HOST, HTTP_PORT, "QuestDB HTTP")
    ok &= check_tcp(HOST, PG_PORT, "QuestDB PG Wire")
    ok &= check_http_tables()

    if ok:
        logger.info("✅  QuestDB probe PASSED")
        sys.exit(0)
    else:
        logger.error("❌  QuestDB probe FAILED — fix issues above")
        sys.exit(1)


if __name__ == "__main__":
    main()
