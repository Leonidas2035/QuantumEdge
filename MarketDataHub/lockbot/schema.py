"""Schema helpers for LockBot market-data events."""

from __future__ import annotations

from typing import Any, Dict

import msgspec

from MarketDataHub.models.events import MarketEvent
from MarketDataHub.models.lockbot_md_contract import SCHEMA_VERSION


class LockbotMarketEvent(MarketEvent):
    schema: str
    topic: str
    ts_event: int
    ts_pub: int
    source: str
    payload: Dict[str, Any]


def encode_lockbot(event: LockbotMarketEvent) -> bytes:
    return msgspec.msgpack.encode(event)


def event_to_dict(event: LockbotMarketEvent) -> Dict[str, Any]:
    return msgspec.structs.asdict(event)

