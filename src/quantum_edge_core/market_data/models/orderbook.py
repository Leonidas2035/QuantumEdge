"""Models for order book aggregate streams."""

from __future__ import annotations

from typing import List, Optional

import msgspec

from quantum_edge_core.market_data.models.events import MarketEvent


DEPTH_EVENT_TYPE = "depth_l2"
WALLS_EVENT_TYPE = "walls"


class DepthLevel(msgspec.Struct):
    price: float
    qty: float


class WallLevel(msgspec.Struct):
    price: float
    qty: float
    notional: Optional[float] = None
    distance_bps: Optional[float] = None


class DepthL2Event(MarketEvent):
    bids: List[DepthLevel]
    asks: List[DepthLevel]
    mid: Optional[float] = None
    spread: Optional[float] = None


class WallsSummary(msgspec.Struct):
    count_bid_walls: int
    count_ask_walls: int
    max_wall_qty: float
    nearest_wall_distance_bps: Optional[float]


class WallsEvent(MarketEvent, kw_only=True):
    bids_walls: List[WallLevel]
    asks_walls: List[WallLevel]
    threshold_qty: Optional[float] = None
    threshold_notional_usd: Optional[float] = None
    summary: WallsSummary


def encode_orderbook(event: MarketEvent) -> bytes:
    return msgspec.msgpack.encode(event)


def decode_orderbook(raw: bytes, type_: type[MarketEvent]) -> MarketEvent:
    return msgspec.msgpack.decode(raw, type=type_)
