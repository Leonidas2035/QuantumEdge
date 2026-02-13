#!/usr/bin/env python3
"""End-to-end smoke test for MarketDataHub + QuestDB + L2 replay."""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
import time
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus
from urllib.request import urlopen

from market_data.models import L1Event, L2Envelope, Priority
from market_data.hub import MarketDataHubService
from strategies.scalper_v1.bot.market_data.hub_source import HubMarketDataSource, HubSnapshotClient

ROOT = Path(__file__).resolve().parent.parent
QUESTDB_HOST = "127.0.0.1"
QUESTDB_HTTP_PORT = 9000
QUESTDB_ILP_PORT = 9009
SPOOL_DIR = Path("spool") / "l2"
SPAWL_SERVICE = ROOT / "tools" / "replay_spool.py"
SCHEMA_SCRIPT = ROOT / "tools" / "questdb_apply_schema.sh"
LOGGER = logging.getLogger("smoke_e2e")


def wait_for_http(url: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urlopen(url, timeout=5) as resp:
                if resp.status == 200:
                    return
        except Exception:
            time.sleep(1.0)
    raise RuntimeError(f"Timed out waiting for HTTP {url}")


def questdb_count(table: str) -> int:
    query = quote_plus(f"select count(*) from {table}")
    url = f"http://{QUESTDB_HOST}:{QUESTDB_HTTP_PORT}/exec?query={query}"
    with urlopen(url, timeout=5) as resp:
        data = json.load(resp)
    try:
        return int(data["query"]["results"][0]["count"])
    except Exception as exc:
        raise RuntimeError(f"Failed to parse QuestDB response: {exc}")


async def _publish_l1_events(service: MarketDataHubService, symbol: str, count: int = 3) -> None:
    for _ in range(count):
        seq = service.bus.assign_sequence(symbol, "l1")
        event = L1Event(
            ts_ns=time.time_ns(),
            symbol=symbol,
            event_type="l1",
            seq=seq,
            priority=Priority.L1,
            best_bid=100.0,
            best_ask=100.5,
            bid_size=1.1,
            ask_size=1.2,
        )
        await service.bus.publish(event)
        await asyncio.sleep(0.2)


async def _collect_sub_events(source: HubMarketDataSource, limit: int = 2) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    async for payload in source.stream():
        collected.append(payload)
        if len(collected) >= limit:
            break
    return collected


async def run_smoke() -> None:
    service = MarketDataHubService()
    service.feeds = []
    service_task = asyncio.create_task(service.start())
    source = None
    try:
        await asyncio.sleep(1.0)
        symbol = service.config.symbols[0]

        source_cfg = {
            "hub": {
                "pub_endpoint": service.config.zmq.endpoint,
                "snapshot_endpoint": service.config.snapshot.endpoint,
                "topics": [f"{symbol}:l1"],
            }
        }
        source = HubMarketDataSource([symbol], source_cfg)
        await source.start()
        collect_task = asyncio.create_task(_collect_sub_events(source, limit=2))

        await _publish_l1_events(service, symbol)
        events = await collect_task
        if not events:
            raise RuntimeError("Hot path subscriber did not receive events.")
        LOGGER.info("Received %d HOT events via ZMQ", len(events))

        snapshot_client = HubSnapshotClient(service.config.snapshot.endpoint)
        try:
            snapshot = await snapshot_client.request(symbol, "l1")
        finally:
            snapshot_client.close()
        if not (snapshot and snapshot.ok and snapshot.payload):
            raise RuntimeError("Snapshot recovery path failed.")

        await asyncio.sleep(1.0)

        rows = questdb_count("market_l1")
        if rows == 0:
            raise RuntimeError("Warm path did not persist market_l1 rows.")
        LOGGER.info("Warm path rows in market_l1: %d", rows)

        if not SPOOL_DIR.exists():
            raise RuntimeError(f"Missing spool directory {SPOOL_DIR}")
        l2_event = L2Envelope(
            ts_ns=time.time_ns(),
            entity="equity",
            schema_ver=1,
            seq=service.bus.assign_sequence("", "equity"),
            event_id=str(uuid.uuid4()),
            source="smoke_e2e",
            payload={"equity": 400.0, "balance": 395.0, "available": 380.0, "currency": "USDT"},
        )
        if not service.writer:
            raise RuntimeError("TSDB writer is not enabled.")
        await service.writer.enqueue_l2(l2_event)
        await asyncio.sleep(1.0)

        if not any(SPOOL_DIR.rglob("*.jsonl.gz")):
            raise RuntimeError("No L2 spool files found after enqueuing event.")

        subprocess.run(
            [sys.executable, str(SPAWL_SERVICE), "--spool-dir", str(SPOOL_DIR), "--quest-host", QUESTDB_HOST, "--ilp-port", str(QUESTDB_ILP_PORT)],
            check=True,
            cwd=ROOT,
        )

        l2_rows = questdb_count("l2_equity")
        if l2_rows == 0:
            raise RuntimeError("Replay did not ingest any L2 rows.")
        LOGGER.info("Replay produced %d rows in l2_equity", l2_rows)

        print("SMOKE PASS: Hot/Warm/L2 pipelines working.")
    finally:
        if source:
            await source.stop()
        await service.stop()
        with suppress(asyncio.CancelledError):
            await service_task


def start_questdb() -> subprocess.CompletedProcess:
    LOGGER.info("Starting QuestDB via docker compose ...")
    return subprocess.run([str(ROOT / "deploy" / "questdb" / "up.sh")], check=True, cwd=ROOT)


def stop_questdb() -> None:
    LOGGER.info("Stopping QuestDB")
    subprocess.run([str(ROOT / "deploy" / "questdb" / "down.sh")], check=True, cwd=ROOT)


def apply_schema() -> None:
    LOGGER.info("Applying QuestDB schema")
    subprocess.run([str(SCHEMA_SCRIPT), QUESTDB_HOST, str(QUESTDB_HTTP_PORT)], check=True, cwd=ROOT)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    start_questdb()
    try:
        wait_for_http(f"http://{QUESTDB_HOST}:{QUESTDB_HTTP_PORT}/health", timeout=30.0)
        apply_schema()
        asyncio.run(run_smoke())
    finally:
        stop_questdb()


if __name__ == "__main__":
    main()
