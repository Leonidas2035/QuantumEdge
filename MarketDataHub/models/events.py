"""Typed event models used by the MarketDataHub."""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict

import msgspec


class Priority(str, Enum):
    L0 = "L0"
    L1 = "L1"
    L2 = "L2"


class MarketEvent(msgspec.Struct):
    ts_ns: int
    symbol: str
    event_type: str
    seq: int
    priority: Priority


class TradeEvent(MarketEvent):
    price: float
    size: float
    taker_side: str


class L1Event(MarketEvent):
    best_bid: float
    best_ask: float
    bid_size: float
    ask_size: float


class Bar1sEvent(MarketEvent):
    open: float
    high: float
    low: float
    close: float
    volume: float
    trades: int


class HeartbeatEvent(MarketEvent):
    peer: str
    extra: Dict[str, Any]


def encode_event(event: MarketEvent) -> bytes:
    """Encode a market event to MessagePack."""
    return msgspec.msgpack.encode(event)


def decode_event(data: bytes, type_: type[MarketEvent]) -> MarketEvent:
    """Decode a MessagePack blob into a typed event."""
    return msgspec.msgpack.decode(data, type=type_)
