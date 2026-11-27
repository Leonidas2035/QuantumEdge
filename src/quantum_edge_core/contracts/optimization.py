"""Optimization Master Roadmap v1.0 canonical Pydantic v2 contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


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


__all__ = [
    "DecisionAuditV1",
    "RequestStatus",
    "ThinkingLevel",
    "TransportChannel",
    "UsageRecordV1",
]
