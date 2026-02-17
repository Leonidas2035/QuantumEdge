from __future__ import annotations

import asyncio
import time
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Callable

from bot.storage.event_bus import EventBus
from bot.storage.spooler import Spooler

TABLE_SCHEMAS = {
    "market_trades_raw": {
        "tags": ["symbol", "side"],
        "fields": ["price", "qty", "trade_id"],
        "ts_field": "ts",
    },
    "market_l1": {
        "tags": ["symbol"],
        "fields": ["bid", "ask", "bid_sz", "ask_sz"],
        "ts_field": "ts",
    },
    "bars_1s": {
        "tags": ["symbol"],
        "fields": ["open", "high", "low", "close", "volume", "trades"],
        "ts_field": "ts",
    },
    "bars_1m": {
        "tags": ["symbol"],
        "fields": ["open", "high", "low", "close", "volume", "trades"],
        "ts_field": "ts",
    },
    "signals": {
        "tags": ["bot_id", "symbol", "signal", "model"],
        "fields": ["score"],
        "ts_field": "ts",
    },
    "orders": {
        "tags": [
            "bot_id",
            "symbol",
            "side",
            "type",
            "status",
            "client_order_id",
            "exchange_order_id",
        ],
        "fields": ["qty", "price"],
        "ts_field": "ts",
    },
    "fills": {
        "tags": ["bot_id", "symbol", "client_order_id", "fee_asset"],
        "fields": ["price", "qty", "fee"],
        "ts_field": "ts",
    },
    "positions": {
        "tags": ["bot_id", "symbol"],
        "fields": ["position", "entry_price", "unrealized_pnl", "leverage"],
        "ts_field": "ts",
    },
    "equity": {
        "tags": ["bot_id"],
        "fields": ["equity", "balance", "drawdown"],
        "ts_field": "ts",
    },
    "risk_events": {
        "tags": ["bot_id", "symbol", "level"],
        "fields": ["message"],
        "ts_field": "ts",
    },
}


def _escape_tag(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(",", "\\,")
        .replace("=", "\\=")
        .replace(" ", "\\ ")
    )


def _escape_field_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _format_field(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return f"{value}i"
    if isinstance(value, float):
        return f"{value}"
    if isinstance(value, str):
        return f'"{_escape_field_string(value)}"'
    return f'"{_escape_field_string(str(value))}"'


def event_to_ilp_line(event: Dict[str, Any]) -> Optional[str]:
    table = event.get("table")
    if not table or table not in TABLE_SCHEMAS:
        return None
    schema = TABLE_SCHEMAS[table]
    tags = []
    for tag in schema["tags"]:
        val = event.get(tag)
        if val is None or val == "":
            continue
        tags.append(f"{tag}={_escape_tag(str(val))}")
    fields = []
    for field in schema["fields"]:
        val = event.get(field)
        if val is None:
            continue
        field_val = _format_field(val)
        if field_val is None:
            continue
        fields.append(f"{field}={field_val}")
    if not fields:
        return None
    line = table
    if tags:
        line += "," + ",".join(tags)
    line += " " + ",".join(fields)
    ts_val = event.get(schema["ts_field"]) or event.get("ts_ms") or event.get("ts")
    if ts_val is not None:
        try:
            ts_ns = int(float(ts_val)) * 1_000_000
            line += f" {ts_ns}"
        except Exception:
            pass
    return line


@dataclass
class IlpWriterStats:
    batches: int = 0
    events: int = 0
    failures: int = 0
    last_write_at: Optional[float] = None


class QuestDbIlpWriter:
    """QuestDB ILP writer over HTTP with retries and spool fallback."""

    def __init__(
        self,
        ilp_http_url: str,
        batch_rows: int,
        flush_interval_ms: int,
        max_retries: int = 5,
        base_backoff_ms: int = 200,
        max_backoff_ms: int = 5000,
        spooler: Optional[Spooler] = None,
        transport: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.ilp_http_url = ilp_http_url
        self.batch_rows = max(int(batch_rows), 1)
        self.flush_interval_ms = max(int(flush_interval_ms), 1)
        self.max_retries = max(int(max_retries), 0)
        self.base_backoff_ms = max(int(base_backoff_ms), 10)
        self.max_backoff_ms = max(int(max_backoff_ms), self.base_backoff_ms)
        self.spooler = spooler
        self._transport = transport or self._send_http_payload
        self.stats = IlpWriterStats()

    def _send_http_payload(self, payload: str) -> None:
        data = payload.encode("utf-8")
        req = urllib.request.Request(
            self.ilp_http_url, data=data, headers={"Content-Type": "text/plain"}
        )
        with urllib.request.urlopen(req, timeout=3) as _:
            return

    async def _send_with_retries(self, payload: str) -> bool:
        attempt = 0
        backoff_ms = self.base_backoff_ms
        while True:
            try:
                await asyncio.to_thread(self._transport, payload)
                return True
            except Exception:
                attempt += 1
                if attempt > self.max_retries:
                    return False
                await asyncio.sleep(backoff_ms / 1000.0)
                backoff_ms = min(backoff_ms * 2, self.max_backoff_ms)

    async def flush_events(self, events: Sequence[Dict[str, Any]]) -> bool:
        lines = []
        for event in events:
            line = event_to_ilp_line(event)
            if line:
                lines.append(line)
        if not lines:
            return True
        payload = "\n".join(lines)
        ok = await self._send_with_retries(payload)
        if not ok and self.spooler:
            self.spooler.spool_events(events)
        self.stats.batches += 1
        self.stats.events += len(events)
        if not ok:
            self.stats.failures += 1
        else:
            self.stats.last_write_at = time.time()
        return ok

    async def run(self, bus: EventBus, stop_event: asyncio.Event) -> None:
        batch: List[Dict[str, Any]] = []
        last_flush = time.time()
        while not stop_event.is_set():
            timeout = max(self.flush_interval_ms / 1000.0, 0.01)
            try:
                event = await asyncio.wait_for(bus.get(), timeout=timeout)
                batch.append(event)
            except asyncio.TimeoutError:
                pass
            now = time.time()
            should_flush = (
                len(batch) >= self.batch_rows
                or (now - last_flush) * 1000 >= self.flush_interval_ms
            )
            if batch and should_flush:
                await self.flush_events(batch)
                batch = []
                last_flush = now
        if batch:
            await self.flush_events(batch)
