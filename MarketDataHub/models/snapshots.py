"""Snapshot request / response models for MarketDataHub."""

from __future__ import annotations

from typing import Optional

import msgspec


class SnapshotRequest(msgspec.Struct):
    symbol: str
    event_type: str
    limit: int = 0


class SnapshotResponse(msgspec.Struct):
    ok: bool
    ts_ns: int
    payload_type: str
    payload: bytes
    note: Optional[str] = None
