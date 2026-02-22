"""Minimal LockBotBTC MarketDataHub contract stub (no trading logic)."""

from __future__ import annotations

from typing import Dict, Iterable, List

import msgspec

from quantum_edge_core.market_data.lockbot.schema import LockbotMarketEvent

LOCKBOT_TOPICS: List[str] = [
    "BTCUSDT:mark_price_1s",
    "BTCUSDT:trades_agg",
    "BTCUSDT:ohlcv_1m",
    "BTCUSDT:ohlcv_5m",
    "BTCUSDT:ohlcv_15m",
    "BTCUSDT:funding_rate",
    "BTCUSDT:force_order",
    "BTCUSDT:vwap_d",
    "BTCUSDT:vwap_bands_d",
    "BTCUSDT:avwap",
    "BTCUSDT:liq_heatmap",
]


def decode_lockbot_event(raw: bytes) -> LockbotMarketEvent:
    """Decode a LockBot market-data event (MessagePack)."""
    return msgspec.msgpack.decode(raw, type=LockbotMarketEvent)


def extract_payload(event: LockbotMarketEvent) -> Dict[str, object]:
    """Return the payload dict for downstream use."""
    return dict(event.payload)


def topics_for_symbol(symbol: str) -> Iterable[str]:
    return [topic.replace("BTCUSDT", symbol) for topic in LOCKBOT_TOPICS]
