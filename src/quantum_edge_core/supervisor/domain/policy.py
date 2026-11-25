"""
Policy Manager.
Translates AI Strategy decisions into concrete Trading Bot Contracts.
Enforces Hard Risk Overrides.
"""

from __future__ import annotations
from typing import Dict, Any

from quantum_edge_core.supervisor.domain.models import (
    PolicyContract,
    RiskVerdict,
    RiskLevel,
    TradingMode,
)


class PolicyManager:
    """
    Manages the active policy state and transitions.
    """

    def __init__(self):
        # Default Safe Policy
        self.active_policy = PolicyContract(
            mode=TradingMode.NEUTRAL,
            long_allowed=True,
            short_allowed=True,
            max_leverage=10.0,
            min_order_size=10.0,
            max_position_size=1000.0,
            risk_multiplier=1.0,
            volatility_scalar=1.0,
            close_only=False,
        )

    def apply_ai_decision(self, ai_output: Dict[str, Any]) -> PolicyContract:
        """
        Updates policy based on AI output.
        AI output expected to have keys like:
        - sentiment: "bullish" | "bearish" | "neutral"
        - confidence: float 0-1
        - suggested_leverage: float
        - regime: "trend" | "range" | "volatile"
        """
        # Create a new proposal based on current defaults, modifying based on AI
        proposal = self.active_policy  # Start with current or default defaults?
        # Better to reconstruct from clean slate or defaults to avoid drift?
        # Let's clone
        import copy

        proposal = copy.copy(self.active_policy)

        # Reset constraints
        proposal.close_only = False
        proposal.risk_multiplier = (
            1.0  # Reset to default, let Risk Engine clamp it down again if needed
        )
        proposal.volatility_scalar = float(ai_output.get("volatility_scalar", 1.0))
        proposal.ai_reasoning = ai_output.get("reasoning", "")
        proposal.ai_confidence = float(ai_output.get("confidence", 0.0))

        sentiment = ai_output.get("sentiment", "neutral").lower()
        if sentiment == "bullish":
            proposal.long_allowed = True
            proposal.short_allowed = False
        elif sentiment == "bearish":
            proposal.long_allowed = False
            proposal.short_allowed = True
        else:  # Neutral
            proposal.long_allowed = True
            proposal.short_allowed = True

        regime = ai_output.get("regime", "range").lower()
        if regime == "volatile":
            proposal.mode = TradingMode.PASS
            proposal.max_leverage = 5.0  # Reduce leverage in volatility
        elif regime == "trend":
            proposal.mode = TradingMode.SCALP  # Example mapping
        else:
            proposal.mode = TradingMode.NEUTRAL

        # Param overrides
        if "suggested_leverage" in ai_output:
            proposal.max_leverage = min(
                float(ai_output["suggested_leverage"]), 20.0
            )  # Cap absolute hard limit here too?

        self.active_policy = proposal
        return proposal

    def enforce_hard_risk(
        self, verdict: RiskVerdict, policy: PolicyContract
    ) -> PolicyContract:
        """
        Applies Hard Risk overrides to the proposed policy.
        """
        if verdict.level == RiskLevel.NORMAL:
            return policy

        # Warning Level -> Reduce Only, Lower Leverage, Half Risk Multiplier
        if verdict.level == RiskLevel.WARNING:
            # "Force risk_multiplier = 0.5"
            policy.risk_multiplier = 0.5
            policy.ai_reasoning = f"RISK OVERRIDE: {verdict.reason}"
            # User request: "If VERDICT == WARNING: Force risk_multiplier = 0.5"
            # It doesn't explicitly say "Close Only", but "Warning" usually implies caution.
            # I will keep logic permissive for trading but reduced size via multiplier.

        # Critical Level -> Halt / Freeze / Close All
        if verdict.level == RiskLevel.CRITICAL:
            # "Force PolicyContract(mode='PASS', can_trade=False)"
            policy.mode = TradingMode.PASS  # or HALT which leads to stop
            policy.close_only = True
            policy.long_allowed = False
            policy.short_allowed = False
            policy.max_position_size = 0.0
            policy.risk_multiplier = 0.0
            policy.volatility_scalar = 0.0  # Force zero
            policy.ai_reasoning = f"CRITICAL RISK HALT: {verdict.reason}"

        return policy
