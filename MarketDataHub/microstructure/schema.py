"""Microstructure schema for MarketDataHub events."""

from __future__ import annotations

from typing import Optional

import msgspec

from MarketDataHub.models.events import MarketEvent


MICROSTRUCTURE_EVENT_TYPE = "microstructure.v1"


class MicrostructureEvent(MarketEvent):
    ts_event: int
    ts_ingest: int
    best_bid_px: float
    best_bid_qty: float
    best_ask_px: float
    best_ask_qty: float
    ofi_raw: float
    ofi_z: float
    ofi_ma5: float
    spread_bps: float
    top_qty_sum: float
    trade_rate_1s: Optional[float]
    volume_1s: Optional[float]
    is_gap: bool
    is_resynced: bool
    schema_version: int = 1


def encode_microstructure(event: MicrostructureEvent) -> bytes:
    """Encode microstructure event to MessagePack."""
    return msgspec.msgpack.encode(event)
