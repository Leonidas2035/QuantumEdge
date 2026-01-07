"""QuestDB ILP writer with micro-batch buffering for warm path."""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import logging
import time
from typing import Any, Dict, Iterable, List, Optional

from MarketDataHub.config import L2Config, TsdbConfig
from MarketDataHub.models import Bar1sEvent, ENTITY_TABLE_MAP, L1Event, L2Envelope, MarketEvent, MicrostructureEvent
from MarketDataHub.spool.l2_spooler import L2Spooler


@dataclasses.dataclass
class TsdbMetrics:
    written_rows: int = 0
    dropped_rows: int = 0
    last_flush_ts: Optional[float] = None
    errors: int = 0
    l2_spooled_total: int = 0
    l2_buffered_total: int = 0
    l2_written_total: int = 0
    l2_write_errors_total: int = 0
    l2_buffer_overflow_total: int = 0


class QuestILPWriter:
    """Micro-batch ILP writer with bounded caches and L2 support."""

    def __init__(self, tsdb_config: TsdbConfig, l2_config: L2Config) -> None:
        self._config = tsdb_config
        self._l2_config = l2_config
        self._l1_cache: Dict[str, L1Event] = {}
        self._bars_queue: asyncio.Queue[Bar1sEvent] = asyncio.Queue(maxsize=self._config.bars_queue_max)
        self._micro_queue: asyncio.Queue[MicrostructureEvent] = asyncio.Queue(maxsize=self._config.bars_queue_max)
        self._l2_buffer: List[str] = []
        self._l2_buffer_max = self._l2_config.buffer_max
        self._l2_spooler: Optional[L2Spooler] = (
            L2Spooler(self._l2_config) if self._l2_config.enabled else None
        )
        self._l2_overflow_log_ts = 0.0
        self._l2_overflow_interval = 5.0
        self._batch_rows = self._config.batch_rows
        self._flush_interval = self._config.flush_interval_ms / 1000.0
        self._metrics = TsdbMetrics()
        self._task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._writer: Optional[asyncio.StreamWriter] = None
        self._reader: Optional[asyncio.StreamReader] = None
        self._backoff = 1.0
        self._running = False

    @property
    def metrics(self) -> TsdbMetrics:
        return self._metrics

    async def start(self) -> None:
        if not self._running:
            self._running = True
            self._task = asyncio.create_task(self._flush_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await self._close_connection()
        if self._l2_spooler:
            self._l2_spooler.close()

    async def enqueue(self, event: MarketEvent) -> None:
        if isinstance(event, L1Event):
            self._l1_cache[event.symbol] = event
        elif isinstance(event, Bar1sEvent):
            try:
                self._bars_queue.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    self._bars_queue.get_nowait()
                    self._bars_queue.put_nowait(event)
                    self._metrics.dropped_rows += 1
                except asyncio.QueueEmpty:
                    self._metrics.dropped_rows += 1
        elif isinstance(event, MicrostructureEvent):
            try:
                self._micro_queue.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    self._micro_queue.get_nowait()
                    self._micro_queue.put_nowait(event)
                    self._metrics.dropped_rows += 1
                except asyncio.QueueEmpty:
                    self._metrics.dropped_rows += 1

    async def enqueue_l2(self, event: L2Envelope) -> None:
        if not self._l2_spooler:
            return
        self._l2_spooler.append(event)
        self._metrics.l2_spooled_total += 1
        line = self._format_l2_line(event)
        if not line:
            return
        if len(self._l2_buffer) >= self._l2_buffer_max:
            self._metrics.l2_buffer_overflow_total += 1
            self._maybe_log_l2_overflow()
            return
        self._l2_buffer.append(line)
        self._metrics.l2_buffered_total += 1

    async def _flush_loop(self) -> None:
        while self._running:
            await asyncio.sleep(self._flush_interval)
            await self._flush()

    async def _flush(self) -> None:
        async with self._lock:
            lines, l2_count = self._build_batch()
            if not lines:
                return
            try:
                await self._write_batch(lines)
                self._metrics.written_rows += len(lines)
                self._metrics.l2_written_total += l2_count
                self._metrics.last_flush_ts = time.time()
            except Exception as exc:
                logging.warning("QuestDB ILP flush failed: %s", exc)
                self._metrics.errors += 1
                if l2_count:
                    self._metrics.l2_write_errors_total += l2_count
                await self._close_connection()

    def _build_batch(self) -> tuple[List[str], int]:
        lines: List[str] = []
        lines.extend(self._build_l1_lines())
        if len(lines) < self._batch_rows:
            lines.extend(self._build_bar_lines(self._batch_rows - len(lines)))
        if len(lines) < self._batch_rows:
            lines.extend(self._build_micro_lines(self._batch_rows - len(lines)))
        l2_lines = []
        if self._l2_buffer:
            l2_lines = self._drain_l2_buffer(self._batch_rows - len(lines))
            lines.extend(l2_lines)
        return lines, len(l2_lines)

    def queue_depth(self) -> int:
        return self._bars_queue.qsize() + self._micro_queue.qsize() + len(self._l2_buffer)

    @staticmethod
    def _format_l1(event: L1Event) -> str:
        ts = event.ts_ns
        return (
            f"market_l1,symbol={event.symbol} "
            f"bid={event.best_bid},ask={event.best_ask},bid_sz={event.bid_size},ask_sz={event.ask_size} {ts}"
        )

    @staticmethod
    def _format_bar(event: Bar1sEvent) -> str:
        ts = event.ts_ns
        return (
            f"bars_1s,symbol={event.symbol} "
            f"open={event.open},high={event.high},low={event.low},close={event.close},"
            f"volume={event.volume},trades={int(event.trades)}i {ts}"
        )

    @staticmethod
    def _format_microstructure(event: MicrostructureEvent) -> str:
        ts = event.ts_ns
        measurement = f"microstructure_v1,symbol={event.symbol}"
        fields: Dict[str, Any] = {
            "best_bid_px": event.best_bid_px,
            "best_bid_qty": event.best_bid_qty,
            "best_ask_px": event.best_ask_px,
            "best_ask_qty": event.best_ask_qty,
            "ofi_raw": event.ofi_raw,
            "ofi_z": event.ofi_z,
            "ofi_ma5": event.ofi_ma5,
            "spread_bps": event.spread_bps,
            "top_qty_sum": event.top_qty_sum,
            "trade_rate_1s": event.trade_rate_1s,
            "volume_1s": event.volume_1s,
            "is_gap": event.is_gap,
            "is_resynced": event.is_resynced,
            "schema_version": int(event.schema_version),
            "ts_event": int(event.ts_event),
        }
        field_str = QuestILPWriter._format_fields(fields)
        if not field_str:
            return ""
        return f"{measurement} {field_str} {ts}"

    def _build_l1_lines(self) -> List[str]:
        lines: List[str] = []
        for symbol, event in list(self._l1_cache.items()):
            if len(lines) >= self._batch_rows:
                break
            lines.append(self._format_l1(event))
            self._l1_cache.pop(symbol, None)
        return lines

    def _build_bar_lines(self, limit: int) -> List[str]:
        lines: List[str] = []
        while len(lines) < limit and not self._bars_queue.empty():
            try:
                bar = self._bars_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            lines.append(self._format_bar(bar))
        return lines

    def _build_micro_lines(self, limit: int) -> List[str]:
        lines: List[str] = []
        while len(lines) < limit and not self._micro_queue.empty():
            try:
                event = self._micro_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            line = self._format_microstructure(event)
            if line:
                lines.append(line)
        return lines

    def _drain_l2_buffer(self, limit: int) -> List[str]:
        drained: List[str] = []
        for _ in range(min(limit, len(self._l2_buffer))):
            drained.append(self._l2_buffer.pop(0))
        return drained

    def _format_l2_line(self, event: L2Envelope) -> Optional[str]:
        table = self._l2_table_for_entity(event.entity)
        if not table:
            return None
        tags = []
        if event.symbol:
            tags.append(f"symbol={event.symbol}")
        if event.source:
            tags.append(f"source={event.source}")
        measurement = table + ("," + ",".join(tags) if tags else "")
        fields: Dict[str, Any] = {}
        payload = event.payload or {}
        if event.seq is not None:
            fields["seq"] = event.seq
        if event.event_id:
            fields["event_id"] = event.event_id
        fields["payload_json"] = json.dumps(payload, separators=(",", ":"))
        if event.entity == "fills":
            fields["order_id"] = payload.get("order_id")
            fields["side"] = payload.get("side")
            fields["qty"] = payload.get("qty")
            fields["price"] = payload.get("price")
            fields["fee"] = payload.get("fee")
            fields["pnl"] = payload.get("pnl")
            fields["exchange"] = payload.get("exchange")
            fields["account"] = payload.get("account")
        elif event.entity == "positions":
            fields["side"] = payload.get("side")
            fields["qty"] = payload.get("qty")
            fields["entry_price"] = payload.get("entry_price")
            fields["mark_price"] = payload.get("mark_price")
            fields["unrealized_pnl"] = payload.get("unrealized_pnl")
            fields["leverage"] = payload.get("leverage")
            fields["margin"] = payload.get("margin")
        elif event.entity == "equity":
            fields["equity"] = payload.get("equity")
            fields["balance"] = payload.get("balance")
            fields["available"] = payload.get("available")
            fields["currency"] = payload.get("currency")
        elif event.entity == "risk":
            fields["risk_mode"] = payload.get("risk_mode")
            fields["max_dd"] = payload.get("max_dd")
            fields["exposure"] = payload.get("exposure")
            fields["notes"] = payload.get("notes")
        field_str = self._format_fields(fields)
        if not field_str:
            return None
        return f"{measurement} {field_str} {event.ts_ns}"

    def _format_fields(self, fields: Dict[str, Any]) -> str:
        parts = []
        for key, value in fields.items():
            if value is None:
                continue
            formatted = self._format_value(value)
            if formatted is None:
                continue
            parts.append(f"{key}={formatted}")
        return ",".join(parts)

    @staticmethod
    def _format_value(value: Any) -> Optional[str]:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, int):
            return f"{value}i"
        if isinstance(value, float):
            return repr(value)
        return json.dumps(value)

    def _l2_table_for_entity(self, entity: str) -> Optional[str]:
        return ENTITY_TABLE_MAP.get(entity)

    def _maybe_log_l2_overflow(self) -> None:
        now = time.time()
        if now - self._l2_overflow_log_ts >= self._l2_overflow_interval:
            logging.warning("L2 buffer full; relying on WAL replay for delivery")
            self._l2_overflow_log_ts = now

    async def _write_batch(self, lines: Iterable[str]) -> None:
        await self._ensure_connection()
        payload = "\n".join(lines) + "\n"
        self._writer.write(payload.encode("utf-8"))
        await self._writer.drain()

    async def _ensure_connection(self) -> None:
        if self._writer and not self._writer.is_closing():
            return
        await self._close_connection()
        while self._running:
            try:
                reader, writer = await asyncio.open_connection(self._config.host, self._config.ilp_port)
                self._reader = reader
                self._writer = writer
                self._backoff = 1.0
                logging.info("QuestDB ILP connected to %s:%s", self._config.host, self._config.ilp_port)
                return
            except (OSError, asyncio.TimeoutError) as exc:
                logging.warning("QuestDB ILP connect failed: %s", exc)
                await asyncio.sleep(self._backoff)
                self._backoff = min(self._backoff * 2, 10.0)

    async def _close_connection(self) -> None:
        if self._writer:
            self._writer.close()
            with contextlib.suppress(Exception):
                await self._writer.wait_closed()
        self._writer = None
        self._reader = None
