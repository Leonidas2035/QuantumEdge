from __future__ import annotations

import asyncio
from typing import Optional

from bot.storage.event_bus import EventBus, EventPriority
from bot.storage.spooler import Spooler
from bot.storage.tsdb.questdb_ilp_writer import QuestDbIlpWriter
from bot.storage.tsdb_config import TsdbConfig, load_tsdb_config


class TsdbSink:
    def __init__(self, cfg: TsdbConfig) -> None:
        self.cfg = cfg
        self.enabled = cfg.enabled and cfg.backend == "questdb"
        self._bus: Optional[EventBus] = None
        self._writer: Optional[QuestDbIlpWriter] = None
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()

    def _build_bus(self) -> EventBus:
        return EventBus(max_events=self.cfg.queue.max_events, max_bytes=self.cfg.queue.max_bytes)

    def _build_writer(self) -> QuestDbIlpWriter:
        spooler = None
        if self.cfg.spool.enabled:
            spooler = Spooler(
                base_dir=self.cfg.spool.path,
                max_bytes=self.cfg.spool.max_bytes,
                retention_days=self.cfg.spool.retention_days,
                max_file_bytes=self.cfg.spool.max_file_bytes,
                rotation_minutes=self.cfg.spool.rotation_minutes,
            )
        return QuestDbIlpWriter(
            ilp_http_url=self.cfg.questdb.ilp_http_url,
            batch_rows=self.cfg.writer.batch_rows,
            flush_interval_ms=self.cfg.writer.flush_interval_ms,
            max_retries=self.cfg.retry.max_retries,
            base_backoff_ms=self.cfg.retry.base_backoff_ms,
            max_backoff_ms=self.cfg.retry.max_backoff_ms,
            spooler=spooler,
        )

    async def start(self) -> None:
        if not self.enabled or self._task:
            return
        self._bus = self._build_bus()
        self._writer = self._build_writer()
        self._task = asyncio.create_task(self._writer.run(self._bus, self._stop))

    async def publish(self, event: dict, priority: EventPriority = EventPriority.NORMAL) -> bool:
        if not self.enabled:
            return False
        await self.start()
        if not self._bus:
            return False
        return await self._bus.publish(event, priority)

    def close(self) -> None:
        if self._task and not self._stop.is_set():
            self._stop.set()


_SINK: Optional[TsdbSink] = None


def get_tsdb_sink() -> TsdbSink:
    global _SINK
    if _SINK is None:
        _SINK = TsdbSink(load_tsdb_config())
    return _SINK
