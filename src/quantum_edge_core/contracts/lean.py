"""Lightweight, non-brittle trading contracts (Pydantic v2 with extra='ignore')."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class MarketSnapshot(BaseModel):
    """Compact market state snapshot tolerant to feed schema additions."""

    model_config = ConfigDict(extra="ignore")

    symbol: str
    bid: float
    ask: float
    last_price: float
    spread_bps: float
    ts_utc: datetime
    source: str = "bingx_ws"
    is_stale: bool = False
    indicators: dict[str, float | None] = Field(default_factory=dict)


class TradeDecision(BaseModel):
    """Actionable trading decision emitted by the agent or strategy."""

    model_config = ConfigDict(extra="ignore")

    decision_id: str
    symbol: str
    action: Literal["OPEN_LONG", "OPEN_SHORT", "CLOSE_LONG", "CLOSE_SHORT", "HOLD"]
    order_type: Literal["LIMIT", "MARKET"] = "LIMIT"
    entry_price: float | None = None
    sl_price: float | None = None
    tp_price: float | None = None
    margin_vst: float = 0.0
    leverage: int = 10
    qty: float | None = None
    reason: str


class OrderResult(BaseModel):
    """Normalized execution outcome recorded in OrderLedger."""

    model_config = ConfigDict(extra="ignore")

    client_order_id: str
    symbol: str
    action: str
    status: Literal["FILLED", "SUBMITTED", "REJECTED", "UNKNOWN", "SKIPPED_HOLD"]
    exchange_order_id: str | None = None
    filled_qty: float = 0.0
    avg_price: float | None = None
    error: str | None = None
