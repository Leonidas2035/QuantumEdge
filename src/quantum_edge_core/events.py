"""
src/quantum_edge_core/events.py

High-performance event system using msgspec.
Defines the core data structures for the trading system.
"""

from typing import List, Union

import msgspec


class BaseEvent(
    msgspec.Struct,
    tag=True,
    omit_defaults=True,
    forbid_unknown_fields=True,
    kw_only=True,
):
    """
    Base class for all system events.
    Polymorphic tagging allows automatic type discrimination during decoding.
    """

    pass
    priority: str = "L1"
    event_type: str = "unknown"
    seq: int = 0


class Heartbeat(BaseEvent):
    """
    System heartbeat signal.
    """

    component: str
    timestamp: float


class MarketTrade(BaseEvent):
    """
    A public market trade (aggreagted or individual).
    """

    symbol: str
    price: float
    quantity: float
    side: str
    timestamp: float


class OrderBookUpdate(BaseEvent):
    """
    L2 Order Book update snapshot or delta.
    bids/asks are list of [price, qty] or similar structure.
    For simplicity here, we assert they are lists of lists of floats.
    """

    symbol: str
    bids: List[List[float]]
    asks: List[List[float]]
    timestamp: float


class LargeBlockEvent(BaseEvent):
    """
    Significant market trade (Whale Alert).
    """

    symbol: str
    price: float
    quantity: float
    side: str
    timestamp: float


class MicrostructureMetrics(BaseEvent):
    """
    Computed Order Book metrics.
    """

    symbol: str
    imbalance: float
    spread_bps: float
    timestamp: float


class MarketMetrics(BaseEvent):
    """
    Comprehensive Alpha Engine metrics for Regime Switching.
    """

    symbol: str
    regime: str  # RANGE, TREND_UP, TREND_DOWN, VOLATILE
    ofi_1s: float
    vwap: float
    imbalance: float
    whale_activity: float
    timestamp: float


# Define the Union of all event types for the Decoder
Event = Union[
    Heartbeat,
    MarketTrade,
    OrderBookUpdate,
    LargeBlockEvent,
    MicrostructureMetrics,
    MarketMetrics,
]


class EventCodec:
    """
    Standardized Encoder/Decoder for all BaseEvent types.
    """

    _encoder = msgspec.json.Encoder()
    _decoder = msgspec.json.Decoder(Event)

    @classmethod
    def encode(cls, event: BaseEvent) -> bytes:
        return cls._encoder.encode(event)

    @classmethod
    def decode(cls, data: bytes) -> BaseEvent:
        return cls._decoder.decode(data)
