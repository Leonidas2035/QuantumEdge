"""
Core data structures for the AI Scalper Bot.
Optimized for memory efficiency and high-frequency access using slots.
"""

from dataclasses import dataclass
from typing import List, Optional
from enum import Enum


class TradingMode(str, Enum):
    SCALP = "scalp"
    DCA = "dca"
    PASS = "pass"
    NEUTRAL = "neutral"


@dataclass(slots=True)
class MarketTick:
    """
    Represents a single market trade/tick.

    Attributes:
        price (float): The price of the trade.
        quantity (float): The quantity traded.
        timestamp (float): Exchange timestamp in seconds.
        is_buyer_maker (bool): True if the buyer was the maker.
    """

    price: float
    quantity: float
    timestamp: float
    is_buyer_maker: bool


@dataclass(slots=True)
class OrderBookState:
    """
    Represents the state of the order book at a specific point in time.

    Attributes:
        timestamp (float): Local timestamp of update.
        bids (List[List[float]]): List of [price, qty] for bids.
        asks (List[List[float]]): List of [price, qty] for asks.
    """

    timestamp: float
    bids: List[List[float]]
    asks: List[List[float]]


@dataclass(slots=True)
class MarketState:
    """
    Snapshot of the market used for strategy decision making.

    Attributes:
        timestamp (float): Snapshot timestamp.
        best_bid (float): Current best bid price.
        best_ask (float): Current best ask price.
        best_bid_qty (float): Quantity at best bid.
        best_ask_qty (float): Quantity at best ask.
        last_price (float): Last traded price.
        whale_walls (List[dict]): Detected whale side limit orders.
    """

    timestamp: float
    best_bid: float
    best_ask: float
    best_bid_qty: float
    best_ask_qty: float
    last_price: float
    whale_walls: List[dict] = None
    entries_paused: bool = False
    risk_multiplier: float = 1.0
    volume_delta_1m: float = 0.0
    liquidations_1m: int = 0
    atr: float = 0.0
    trading_mode: TradingMode = TradingMode.PASS
    buy_zone_max: float = 0.0
    sell_zone_min: float = 0.0
    vol_index: float = 0.0
    grid_spacing_pct: float = 0.002
