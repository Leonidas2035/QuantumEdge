"""Hard Risk Engine: Pure logic for critical risk limits."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Dict


class RiskAction(str, Enum):
    ALLOW = "ALLOW"
    REDUCE_ONLY = "REDUCE_ONLY"
    KILL_BOT = "KILL_BOT"  # Hard stop


@dataclass
class RiskLimits:
    max_daily_loss: Optional[float] = None
    max_drawdown: Optional[float] = None
    max_exposure: Optional[float] = None


@dataclass
class RiskDecision:
    action: RiskAction
    reason: str


class HardRiskEngine:
    """
    Pure logic risk engine.
    Design Principle: O(1) execution, no I/O, no side effects.
    """

    @staticmethod
    def check(state: Dict[str, float], limits: RiskLimits) -> RiskDecision:
        """
        Evaluates risk based on current state and limits.
        
        state expected keys:
          - equity_start: float
          - equity_current: float
          - max_equity_intraday: float
          - current_exposure: float
        """
        
        equity_start = state.get("equity_start")
        equity_current = state.get("equity_current")
        max_equity = state.get("max_equity_intraday")
        current_exposure = state.get("current_exposure", 0.0)

        # Safety checks for missing data
        if equity_start is None or equity_current is None:
            # If we don't know our equity, we probably shouldn't kill just yet, 
            # OR we should decide what the safe default is. 
            # Assuming "ALLOW" until data arrives, or "REDUCE_ONLY" if paranoic.
            # For this implementation, we allow if data is missing, assuming initialization phase.
            return RiskDecision(RiskAction.ALLOW, "Initializing data")

        # 1. Check Max Daily Loss
        if limits.max_daily_loss is not None:
            daily_loss = equity_start - equity_current
            if daily_loss > limits.max_daily_loss:
                return RiskDecision(
                    RiskAction.KILL_BOT,
                    f"Max Daily Loss exceeded: {daily_loss:.2f} > {limits.max_daily_loss:.2f}"
                )

        # 2. Check Max Drawdown
        if limits.max_drawdown is not None and max_equity is not None:
            drawdown = max_equity - equity_current
            if drawdown > limits.max_drawdown:
                return RiskDecision(
                    RiskAction.KILL_BOT,
                    f"Max Drawdown exceeded: {drawdown:.2f} > {limits.max_drawdown:.2f}"
                )

        # 3. Check Exposure
        if limits.max_exposure is not None:
            if current_exposure > limits.max_exposure:
                return RiskDecision(
                    RiskAction.REDUCE_ONLY,
                    f"Max Exposure exceeded: {current_exposure:.2f} > {limits.max_exposure:.2f}"
                )

        return RiskDecision(RiskAction.ALLOW, "OK")
