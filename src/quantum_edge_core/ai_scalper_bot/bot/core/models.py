"""
Core data structures for the AI Scalper Bot.
Optimized for memory efficiency and high-frequency access using slots.
"""

from dataclasses import dataclass
from typing import List


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
    """

    timestamp: float
    best_bid: float
    best_ask: float
    best_bid_qty: float
    best_ask_qty: float
    last_price: float
