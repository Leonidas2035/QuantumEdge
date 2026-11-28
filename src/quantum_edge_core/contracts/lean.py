"""Lightweight, non-brittle trading contracts (Pydantic v2 with extra='ignore')."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

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
    leverage: int | None = 1
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


class FullMarketContext(BaseModel):
    """Uncompressed, full-fidelity market and account context for external decision agents."""

    model_config = ConfigDict(extra="ignore")

    market_snapshot: MarketSnapshot
    ohlcv_by_tf: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    orderbook: dict[str, Any] = Field(default_factory=dict)
    derivatives: dict[str, Any] = Field(default_factory=dict)
    microstructure_and_liquidity: dict[str, Any] = Field(default_factory=dict)
    account_state: dict[str, Any] = Field(default_factory=dict)
    risk_limits: dict[str, Any] = Field(default_factory=dict)
    raw_extras: dict[str, Any] = Field(default_factory=dict)


from quantum_edge_core.contracts.optimization import (  # noqa: E402, F401
    DecisionAuditV1,
    DecisionEventV1,
    ExternalAdvisoryV1,
    ForecastBundleV1,
    HoldBlockerCode,
    RequestStatus,
    ScreeningDecisionV1,
    ThinkingLevel,
    TradeDecisionV2,
    TransportChannel,
    UsageRecordV1,
)
