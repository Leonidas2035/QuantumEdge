"""L2 contract for MarketDataHub (WAL + replay)."""

from __future__ import annotations

from typing import Any, Dict, Optional, Type

import msgspec


class L2Payload(msgspec.Struct):
    """Base class for structured L2 payloads."""


class FillEvent(L2Payload):
    order_id: Optional[str]
    side: Optional[str]
    qty: Optional[float]
    price: Optional[float]
    fee: Optional[float]
    pnl: Optional[float]
    exchange: Optional[str]
    account: Optional[str]


class PositionEvent(L2Payload):
    symbol: Optional[str]
    side: Optional[str]
    qty: Optional[float]
    entry_price: Optional[float]
    mark_price: Optional[float]
    unrealized_pnl: Optional[float]
    leverage: Optional[float]
    margin: Optional[float]


class EquityEvent(L2Payload):
    equity: Optional[float]
    balance: Optional[float]
    available: Optional[float]
    currency: Optional[str]


class RiskEvent(L2Payload):
    risk_mode: Optional[str]
    max_dd: Optional[float]
    exposure: Optional[float]
    notes: Optional[str]


class L2Envelope(msgspec.Struct):
    ts_ns: int
    entity: str
    symbol: Optional[str] = None
    seq: Optional[int] = None
    event_id: Optional[str] = None
    source: Optional[str] = None
    schema_ver: int = 1
    payload: Dict[str, Any] = {}


L2PayloadType = Type[L2Payload]


def encode_l2(envelope: L2Envelope) -> bytes:
    return msgspec.json.encode(envelope)


def decode_l2(blob: bytes) -> L2Envelope:
    return msgspec.json.decode(blob, type=L2Envelope)
