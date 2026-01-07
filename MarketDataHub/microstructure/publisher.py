"""Publish microstructure snapshots to ZeroMQ and QuestDB."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from MarketDataHub.bus.event_bus import EventBus
from MarketDataHub.ipc.publisher import ZmqPublisher
from MarketDataHub.microstructure.ofi import MicrostructureSnapshot
from MarketDataHub.microstructure.schema import MicrostructureEvent
from MarketDataHub.models import Priority
from MarketDataHub.tsdb.quest_writer import QuestILPWriter


class MicrostructurePublisher:
    """Wraps ZMQ and QuestDB writes for microstructure events."""

    def __init__(
        self,
        publisher: ZmqPublisher,
        bus: EventBus,
        writer: Optional[QuestILPWriter] = None,
        event_type: str = "microstructure.v1",
    ) -> None:
        self._publisher = publisher
        self._bus = bus
        self._writer = writer
        self._event_type = event_type

    def publish(self, snapshot: MicrostructureSnapshot) -> MicrostructureEvent:
        event = MicrostructureEvent(
            ts_ns=snapshot.ts_ingest,
            symbol=snapshot.symbol,
            event_type=self._event_type,
            seq=self._bus.assign_sequence(snapshot.symbol, self._event_type),
            priority=Priority.L1,
            ts_event=snapshot.ts_event,
            ts_ingest=snapshot.ts_ingest,
            best_bid_px=snapshot.best_bid_px,
            best_bid_qty=snapshot.best_bid_qty,
            best_ask_px=snapshot.best_ask_px,
            best_ask_qty=snapshot.best_ask_qty,
            ofi_raw=snapshot.ofi_raw,
            ofi_z=snapshot.ofi_z,
            ofi_ma5=snapshot.ofi_ma5,
            spread_bps=snapshot.spread_bps,
            top_qty_sum=snapshot.top_qty_sum,
            trade_rate_1s=snapshot.trade_rate_1s,
            volume_1s=snapshot.volume_1s,
            is_gap=snapshot.is_gap,
            is_resynced=snapshot.is_resynced,
            schema_version=1,
        )
        self._publisher.publish(event)
        if self._writer:
            try:
                asyncio.create_task(self._writer.enqueue(event))
            except RuntimeError:
                logging.debug("QuestDB writer task scheduling failed for microstructure event")
        return event
