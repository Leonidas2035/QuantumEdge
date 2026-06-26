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


class WhaleWall(BaseEvent):
    """
    Detected large limit order in the local order book.
    """

    side: str  # "BID" or "ASK"
    price: float
    quantity: float


class OrderBookUpdate(BaseEvent):
    """
    L2 Order Book update snapshot or delta.
    bids/asks are list of [price, qty].
    """

    symbol: str
    bids: List[List[float]]
    asks: List[List[float]]
    timestamp: float
    whale_walls: List[WhaleWall] = []
    mid: float = 0.0
    mid_price: float = 0.0
    spread: float = 0.0
    ofi_1s: float = 0.0
    cvd_10s: float = 0.0
    imbalance_top10: float = 0.0


class LiquidationEvent(BaseEvent):
    """
    Forced liquidation event from Binance @forceOrder stream.
    """

    symbol: str
    side: str  # "BUY" or "SELL"
    price: float  # Liquidation price
    qty: float  # Liquidation quantity
    usd_size: float  # Notional value (price * qty)
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


class KlineEvent(BaseEvent):
    """
    A 1-minute kline (candlestick) update from Binance.
    Emitted on every kline WS push; `is_closed` indicates bar completion.
    """

    symbol: str
    interval: str  # e.g. "1m"
    open: float
    high: float
    low: float
    close: float
    volume: float
    trades: int
    is_closed: bool
    price: float  # alias for close — consumed by bot
    quantity: float  # alias for volume — consumed by bot
    timestamp: float  # kline open time in seconds
    taker_side: str = "buy"  # default so bot normalizer doesn't crash
    side: str = "buy"  # alias for AlphaEngine compatibility


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
    WhaleWall,
    OrderBookUpdate,
    LargeBlockEvent,
    LiquidationEvent,
    MicrostructureMetrics,
    KlineEvent,
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
