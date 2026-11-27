"""Uncompressed full market context builder for external strategic agents (e.g. Hermes-GPT)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from quantum_edge_core.contracts.lean import FullMarketContext, MarketSnapshot
from quantum_edge_core.execution.market_context_serializer import (
    MarketContextSerializer,
    SerializationMode,
    SerializedMarketPayload,
)
from quantum_edge_core.execution.risk_guard import RiskConfig


DEFAULT_DERIVATIVE_KEYS: tuple[str, ...] = (
    "funding_rate",
    "next_funding_time",
    "open_interest",
    "mark_price",
    "index_price",
    "long_short_ratio",
)


def _serialize_risk_limits(
    risk_limits: RiskConfig | Mapping[str, Any] | None,
) -> dict[str, Any]:
    if risk_limits is None:
        risk_limits = RiskConfig()
    if isinstance(risk_limits, RiskConfig):
        return {
            "allowed_symbols": list(risk_limits.allowed_symbols),
            "max_margin_vst": risk_limits.max_margin_vst,
            "max_leverage": risk_limits.max_leverage,
            "max_open_positions": risk_limits.max_open_positions,
            "require_sl_on_open": risk_limits.require_sl_on_open,
            "entry_enabled": risk_limits.entry_enabled,
        }
    return dict(risk_limits)


def build_full_market_context(
    market_snapshot: MarketSnapshot | Mapping[str, Any],
    *,
    ohlcv_by_tf: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    orderbook: Mapping[str, Any] | None = None,
    derivatives: Mapping[str, Any] | None = None,
    microstructure_and_liquidity: Mapping[str, Any] | None = None,
    account_state: Mapping[str, Any] | None = None,
    risk_limits: RiskConfig | Mapping[str, Any] | None = None,
    raw_extras: Mapping[str, Any] | None = None,
) -> FullMarketContext:
    """Aggregate complete, untruncated market, liquidity, derivatives, and account state.

    No byte caps, bar-count limits, or orderbook depth slicing are applied.
    Missing derivative metrics remain None rather than 0.0.
    """
    snap = (
        market_snapshot
        if isinstance(market_snapshot, MarketSnapshot)
        else MarketSnapshot.model_validate(market_snapshot)
    )

    ohlcv_out: dict[str, list[dict[str, Any]]] = {}
    if ohlcv_by_tf:
        for tf, bars in ohlcv_by_tf.items():
            ohlcv_out[str(tf)] = [dict(bar) for bar in bars]

    if orderbook is None:
        ob_out: dict[str, Any] = {"bids": [], "asks": [], "timestamp": None}
    else:
        ob_out = dict(orderbook)
        ob_out["bids"] = list(orderbook.get("bids", []))
        ob_out["asks"] = list(orderbook.get("asks", []))
        ob_out.setdefault("timestamp", None)

    deriv_out: dict[str, Any] = {key: None for key in DEFAULT_DERIVATIVE_KEYS}
    if derivatives:
        for k, v in derivatives.items():
            deriv_out[str(k)] = v

    micro_out: dict[str, Any] = (
        dict(microstructure_and_liquidity)
        if microstructure_and_liquidity is not None
        else {}
    )

    acc_out: dict[str, Any] = {
        "free_margin_vst": None,
        "total_equity_vst": None,
        "open_positions": [],
        "open_orders": [],
        "recent_trades": [],
    }
    if account_state:
        acc_out.update(account_state)
        for list_field in ("open_positions", "open_orders", "recent_trades"):
            val = acc_out.get(list_field)
            acc_out[list_field] = [] if val is None else list(val)

    risk_out = _serialize_risk_limits(risk_limits)
    extras_out = dict(raw_extras) if raw_extras is not None else {}

    return FullMarketContext(
        market_snapshot=snap,
        ohlcv_by_tf=ohlcv_out,
        orderbook=ob_out,
        derivatives=deriv_out,
        microstructure_and_liquidity=micro_out,
        account_state=acc_out,
        risk_limits=risk_out,
        raw_extras=extras_out,
    )


def serialize_for_llm(
    ctx: FullMarketContext | Mapping[str, Any],
    *,
    mode: SerializationMode = "hybrid_webp",
    ohlcv_tail: int = 25,
    include_full_liqmap: bool = True,
    serializer: MarketContextSerializer | None = None,
) -> SerializedMarketPayload:
    """Facade serializing a FullMarketContext (or dict) into compact LLM-ready payload."""
    ctx_dict = ctx.model_dump(mode="json") if isinstance(ctx, FullMarketContext) else dict(ctx)
    ser = serializer or MarketContextSerializer()
    return ser.serialize_single_asset(
        ctx_dict,
        mode=mode,
        ohlcv_tail=ohlcv_tail,
        include_full_liqmap=include_full_liqmap,
    )
