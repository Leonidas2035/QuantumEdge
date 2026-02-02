"""Publish LockBot market-data events to ZeroMQ and QuestDB."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Optional

from market_data.bus.event_bus import EventBus
from market_data.ipc.publisher import ZmqPublisher
from market_data.lockbot.schema import LockbotMarketEvent
from market_data.models import Priority
from market_data.models.lockbot_md_contract import SCHEMA_VERSION
from market_data.tsdb.quest_writer import QuestILPWriter


class LockbotPublisher:
    def __init__(
        self,
        publisher: ZmqPublisher,
        bus: EventBus,
        writer: Optional[QuestILPWriter] = None,
        schema_version: str = SCHEMA_VERSION,
    ) -> None:
        self._publisher = publisher
        self._bus = bus
        self._writer = writer
        self._schema_version = schema_version

    def publish(
        self,
        *,
        symbol: str,
        event_type: str,
        payload: Dict[str, Any],
        ts_event_ms: int,
        source: str,
        ts_pub_ms: Optional[int] = None,
        priority: Priority = Priority.L1,
    ) -> LockbotMarketEvent:
        ts_pub = ts_pub_ms if ts_pub_ms is not None else int(time.time() * 1000)
        event = LockbotMarketEvent(
            ts_ns=ts_pub * 1_000_000,
            symbol=symbol,
            event_type=event_type,
            seq=self._bus.assign_sequence(symbol, event_type),
            priority=priority,
            schema=self._schema_version,
            topic=f"{symbol}:{event_type}",
            ts_event=int(ts_event_ms),
            ts_pub=int(ts_pub),
            source=source,
            payload=payload,
        )
        self._publisher.publish(event)
        if self._writer:
            try:
                asyncio.create_task(self._writer.enqueue(event))
            except RuntimeError:
                logging.debug("QuestDB writer task scheduling failed for lockbot event")
        return event

