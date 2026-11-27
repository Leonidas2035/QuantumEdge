"""Optimization Master Roadmap v1.0 canonical Pydantic v2 contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TransportChannel(str, Enum):
    """Supported LLM transport channels for QuantumEdge."""

    GEMINI_INTERACTIONS = "gemini_interactions"
    GEMINI_OPENAI_COMPAT = "gemini_openai_compat"
    AGY_CLI_PROXY = "agy_cli_proxy"


class RequestStatus(str, Enum):
    """Normalized request outcome status."""

    OK = "ok"
    TIMEOUT = "timeout"
    ERROR = "error"
    QUOTA_REACHED = "quota_reached"
    CLIENT_DISCONNECTED = "client_disconnected"


class ThinkingLevel(str, Enum):
    """Reasoning effort levels for Gemini 4 models."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"  # Supported ONLY on gemini-4-neon and gemini-4-hydrogen


class UsageRecordV1(BaseModel):
    """Unified token, latency, and cache-efficiency telemetry record.

    All native token counters default to ``None`` (UNKNOWN) — never ``0`` on
    errors or timeouts.
    """

    model_config = ConfigDict(extra="ignore")

    record_id: str
    ts_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    decision_id: str
    stage: Literal["stage1_screen", "stage2_drill", "single_shot", "shadow_ab", "proxy_legacy"]
    transport: TransportChannel
    model_provider_id: str
    agy_alias: str | None = None
    thinking_level: ThinkingLevel | None = None
    request_status: RequestStatus
    error_code: str | None = None
    retry_of: str | None = None

    # Native counters: None means UNKNOWN (NEVER 0 on errors/timeouts)
    logical_input_tokens: int | None = None
    provider_input_tokens: int | None = None
    cache_read_tokens: int | None = None
    output_tokens: int | None = None
    thinking_tokens: int | None = None
    tool_use_tokens: int | None = None
    total_tokens: int | None = None

    input_chars: int = 0
    output_chars: int = 0
    latency_ms: float
    timeout_ms: float
    is_censored: bool = False  # True when request_status == TIMEOUT
    num_turns: int = 1

    # Hashes for prefix cache audit and reproducibility
    prefix_hash: str | None = None
    context_hash: str | None = None
    snapshot_id: str | None = None
    interaction_id: str | None = None

    @property
    def fresh_input_tokens(self) -> int | None:
        """Uncached input tokens billed at full input rate."""
        if self.provider_input_tokens is None:
            return None
        cached = self.cache_read_tokens or 0
        return max(0, self.provider_input_tokens - cached)

    @property
    def iecu(self) -> float | None:
        """Input-Equivalent Compute Units: fresh + 0.05*cached + 5.0*(output+thinking)."""
        fresh = self.fresh_input_tokens
        if fresh is None or self.output_tokens is None or self.thinking_tokens is None:
            return None
        cached = self.cache_read_tokens or 0
        return float(fresh) + 0.05 * float(cached) + 5.0 * float(self.output_tokens + self.thinking_tokens)


class DecisionAuditV1(BaseModel):
    """End-to-end audit record linking decision, context hashes, usage, and PnL outcome."""

    model_config = ConfigDict(extra="ignore")

    decision_id: str
    ts_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    snapshot_id: str
    context_hash: str
    prefix_hash: str
    model_provider_id: str
    agy_alias: str | None = None
    thinking_level: ThinkingLevel
    complexity_score: float | None = None
    usage_record_ids: list[str] = Field(default_factory=list)
    decision_payload: dict[str, Any]
    validator_passed: bool
    validator_reason: str | None = None
    execution_result: dict[str, Any] | None = None
    outcome_pnl_vst: float | None = None
    outcome_mfe_bps: float | None = None
    outcome_mae_bps: float | None = None


class HoldBlockerCode(str, Enum):
    """Explicit Trade-or-Justify blocker codes required whenever action == 'HOLD'."""

    NONE = "NONE"
    SPREAD_EXCEEDS_EDGE = "SPREAD_EXCEEDS_EDGE"
    CONFLICTING_HTF_REGIME = "CONFLICTING_HTF_REGIME"
    NO_LIQ_ASYMMETRY = "NO_LIQ_ASYMMETRY"
    ACTIVE_POSITION_ON_TRACK = "ACTIVE_POSITION_ON_TRACK"
    EXHAUSTION_HIGH_MAE = "EXHAUSTION_HIGH_MAE"


class ScreeningDecisionV1(BaseModel):
    """Stage 1 (low thinking): cross-asset 4-pair screening contract."""

    model_config = ConfigDict(extra="ignore")

    market_regime_summary: str = Field(..., max_length=240)
    action: Literal["HOLD", "DRILL_DOWN", "MANAGE_OPEN_POSITION"]
    candidate_symbol: Literal["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"] | None = None
    hold_blocker_code: HoldBlockerCode = HoldBlockerCode.NONE
    candidate_rationale: str = Field(..., max_length=220)
    estimated_edge_bps: float = Field(0.0, ge=-500.0, le=2000.0)

    @model_validator(mode="after")
    def _validate_candidate(self) -> ScreeningDecisionV1:
        if self.action in ("DRILL_DOWN", "MANAGE_OPEN_POSITION") and not self.candidate_symbol:
            raise ValueError(
                "candidate_symbol is required for DRILL_DOWN / MANAGE_OPEN_POSITION."
            )
        if self.action == "HOLD" and self.hold_blocker_code == HoldBlockerCode.NONE:
            raise ValueError(
                "Trade-or-Justify: HOLD requires an explicit hold_blocker_code != NONE."
            )
        return self


class TradeDecisionV2(BaseModel):
    """Stage 2 causal decision schema (reasoning fields strictly precede action)."""

    model_config = ConfigDict(extra="ignore")

    # 1. Causal analysis (strictly BEFORE action)
    regime_read: str = Field(..., min_length=8, max_length=220)
    primary_edge: str = Field(..., min_length=8, max_length=240)
    counter_thesis: str = Field(..., min_length=4, max_length=160)
    invalidation_price: float | None = Field(default=None, gt=0)

    # 2. Strategic action
    action: Literal[
        "OPEN_LONG",
        "OPEN_SHORT",
        "CLOSE_LONG",
        "CLOSE_SHORT",
        "REDUCE",
        "HOLD",
    ]
    symbol: Literal["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
    hold_blocker_code: HoldBlockerCode = HoldBlockerCode.NONE
    thesis_status: Literal["NEW", "CONFIRMED", "WEAKENED", "INVALIDATED"] = "NEW"
    confidence: float = Field(..., ge=0.0, le=1.0)
    evidence_refs: list[str] = Field(default_factory=list, max_length=8)

    # 3. Order parameters
    order_type: Literal["MARKET", "LIMIT"] = "MARKET"
    entry_price: float | None = Field(default=None, gt=0)
    sl_price: float | None = Field(default=None, gt=0)
    tp_price: float | None = Field(default=None, gt=0)
    margin_vst: float | None = Field(default=None, gt=0)
    leverage: int | None = Field(default=None, ge=1, le=20)
    qty: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _validate_causality_and_geometry(self) -> TradeDecisionV2:
        if self.action == "HOLD" and self.hold_blocker_code == HoldBlockerCode.NONE:
            raise ValueError(
                "Trade-or-Justify: HOLD requires a non-NONE hold_blocker_code."
            )
        if self.action in ("OPEN_LONG", "OPEN_SHORT"):
            if self.sl_price is None or self.tp_price is None:
                raise ValueError(
                    "OPEN_LONG/OPEN_SHORT requires explicit sl_price and tp_price."
                )
            if self.order_type == "LIMIT" and self.entry_price is None:
                raise ValueError("LIMIT order requires explicit entry_price.")
            if self.entry_price is not None:
                if self.action == "OPEN_LONG" and not (
                    self.sl_price < self.entry_price < self.tp_price
                ):
                    raise ValueError(
                        "OPEN_LONG requires sl_price < entry_price < tp_price."
                    )
                if self.action == "OPEN_SHORT" and not (
                    self.tp_price < self.entry_price < self.sl_price
                ):
                    raise ValueError(
                        "OPEN_SHORT requires tp_price < entry_price < sl_price."
                    )
        return self

    def to_lean_decision_dict(
        self,
        decision_id: str,
        *,
        active_position_side: Literal["LONG", "SHORT"] | None = None,
    ) -> dict[str, Any]:
        """Convert to a dict compatible with contracts/lean.py::TradeDecision (extra='ignore')."""
        mapped_action: str = self.action
        if self.action == "REDUCE":
            if active_position_side == "SHORT":
                mapped_action = "CLOSE_SHORT"
            elif active_position_side == "LONG":
                mapped_action = "CLOSE_LONG"
            else:
                raise ValueError(
                    "REDUCE requires a known active_position_side ('LONG' or 'SHORT')."
                )
        return {
            "decision_id": decision_id,
            "symbol": self.symbol,
            "action": mapped_action,
            "order_type": self.order_type,
            "entry_price": self.entry_price,
            "stop_loss": self.sl_price,
            "take_profit": self.tp_price,
            "sl_price": self.sl_price,
            "tp_price": self.tp_price,
            "margin_vst": float(self.margin_vst) if self.margin_vst is not None else 0.0,
            "leverage": int(self.leverage) if self.leverage is not None else 1,
            "qty": self.qty,
            "confidence": self.confidence,
            "reason": f"[{self.thesis_status}] {self.primary_edge} | CT: {self.counter_thesis}",
        }


__all__ = [
    "DecisionAuditV1",
    "HoldBlockerCode",
    "RequestStatus",
    "ScreeningDecisionV1",
    "ThinkingLevel",
    "TradeDecisionV2",
    "TransportChannel",
    "UsageRecordV1",
]
