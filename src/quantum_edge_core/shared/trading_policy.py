"""Shared protocol structs for Supervisor ↔ Bot communication.

Uses ``msgspec`` for zero-copy MessagePack serialisation on the
ZMQ command bus (port 5556).

Encoding::

    import msgspec.msgpack
    raw = msgspec.msgpack.encode(policy)   # bytes

Decoding::

    policy = msgspec.msgpack.decode(raw, type=TradingPolicy)
"""

from __future__ import annotations

import time

import msgspec


class TradingPolicy(msgspec.Struct, frozen=True):
    """Concrete trading directive from Supervisor → Bot.

    Replaces free-form JSON with strict typed fields and
    concrete price levels for zone-based strategies.
    """

    timestamp: float = msgspec.field(default_factory=time.time)
    strategy_mode: str = "PASS"  # SCALP | DCA | PASS
    risk_multiplier: float = 1.0  # 0.0 – 1.0
    buy_zone_max: float = 0.0  # Max price for BUY entries
    sell_zone_min: float = 0.0  # Min price for SELL / TP
    reasoning: str = ""  # LLM reasoning (human-readable)
    market_regime: str = "ranging"
    grid_bias: str = "neutral"
    recommended_grid_top: float = 0.0
    recommended_grid_bottom: float = 0.0
    capital_exposure_pct: float = 1.0
    grid_spacing_multiplier: float = 1.0
