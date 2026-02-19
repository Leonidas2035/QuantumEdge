"""Hard Risk Engine: Stateful implementation for supervisor."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from enum import Enum
# Circular import prevention if needed, but RiskStateSnapshot is in supervisor.state
from typing import TYPE_CHECKING, Any, Dict, Optional, Union

if TYPE_CHECKING:
    from supervisor.state import RiskStateSnapshot


class RiskAction(str, Enum):
    ALLOW = "ALLOW"
    REDUCE_ONLY = "REDUCE_ONLY"
    KILL_BOT = "KILL_BOT"  # Hard stop


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    LIMIT_MAKER = "LIMIT_MAKER"


@dataclass
class OrderRequest:
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    price: Optional[float] = None
    notional: Optional[float] = None
    leverage: Optional[float] = None
    is_reduce_only: bool = False


@dataclass
class RiskDecision:
    allowed: bool
    code: str
    reason: str
    action: RiskAction


class HardRiskEngine:
    def __init__(
        self, risk_config, risk_state, logger, event_logger, trust_policy: bool
    ):
        self.config = risk_config
        self.state = risk_state
        self.logger = logger
        self.event_logger = event_logger
        self.trust_policy = trust_policy

    def update_from_heartbeat(self, payload: Any):
        """Update internal state from heartbeat payload."""
        if not payload:
            return

        equity = None
        realized_pnl = None

        # Helper to get metrics if available
        metrics = getattr(payload, "metrics", {}) or {}
        if not isinstance(metrics, dict):
            metrics = {}

        if hasattr(payload, "equity"):
            equity = payload.equity
        elif isinstance(payload, dict):
            equity = payload.get("equity")

        # New Schema doesn't send equity in metrics, but if it did:
        if equity is None:
            equity = metrics.get("equity")

        if hasattr(payload, "realized_pnl_today"):
            realized_pnl = payload.realized_pnl_today
        elif isinstance(payload, dict):
            realized_pnl = payload.get("realized_pnl_today")

        # Map pnl_session to realized_pnl if missing
        if realized_pnl is None:
            realized_pnl = metrics.get("pnl_session")

        if equity is not None:
            self.state.equity_now = float(equity)
            if self.state.equity_start is None:
                self.state.equity_start = self.state.equity_now

            if (
                self.state.max_equity_intraday is None
                or self.state.equity_now > self.state.max_equity_intraday
            ):
                self.state.max_equity_intraday = self.state.equity_now

            if (
                self.state.min_equity_intraday is None
                or self.state.equity_now < self.state.min_equity_intraday
            ):
                self.state.min_equity_intraday = self.state.equity_now

        if realized_pnl is not None:
            self.state.realized_pnl_today = float(realized_pnl)

        self.check_limits()

    def check_limits(self):
        """Check limits and halt if necessary."""
        if self.state.halted:
            return

        if self.state.equity_start and self.state.equity_now is not None:
            # Daily Loss
            daily_loss = self.state.equity_start - self.state.equity_now
            if (
                hasattr(self.config, "max_daily_loss_abs")
                and self.config.max_daily_loss_abs is not None
                and daily_loss > self.config.max_daily_loss_abs
            ):
                self.halt(
                    f"Max Daily Loss Abs exceeded: {daily_loss} > {self.config.max_daily_loss_abs}"
                )
                return

            if (
                hasattr(self.config, "max_daily_loss_pct")
                and self.config.max_daily_loss_pct is not None
                and self.state.equity_start > 0
            ):
                loss_pct = daily_loss / self.state.equity_start
                if loss_pct > self.config.max_daily_loss_pct:
                    self.halt(
                        f"Max Daily Loss Pct exceeded: {loss_pct:.2%} > {self.config.max_daily_loss_pct:.2%}"
                    )
                    return

        if self.state.max_equity_intraday and self.state.equity_now is not None:
            # Drawdown
            drawdown = self.state.max_equity_intraday - self.state.equity_now
            if (
                hasattr(self.config, "max_drawdown_abs")
                and self.config.max_drawdown_abs is not None
                and drawdown > self.config.max_drawdown_abs
            ):
                self.halt(
                    f"Max Drawdown Abs exceeded: {drawdown} > {self.config.max_drawdown_abs}"
                )
                return

            if (
                hasattr(self.config, "max_drawdown_pct")
                and self.config.max_drawdown_pct is not None
                and self.state.max_equity_intraday > 0
            ):
                dd_pct = drawdown / self.state.max_equity_intraday
                if dd_pct > self.config.max_drawdown_pct:
                    self.halt(
                        f"Max Drawdown Pct exceeded: {dd_pct:.2%} > {self.config.max_drawdown_pct:.2%}"
                    )
                    return

    def halt(self, reason: str):
        self.state.halted = True
        self.state.halt_reason = reason
        if self.logger:
            self.logger.critical(f"RISK ENGINE HALT: {reason}")

    def evaluate_order(self, order: OrderRequest) -> RiskDecision:
        if self.state.halted:
            return RiskDecision(
                False,
                "HALTED",
                f"System Halted: {self.state.halt_reason}",
                RiskAction.KILL_BOT,
            )

        # Simple check for now
        return RiskDecision(True, "ALLOWED", "OK", RiskAction.ALLOW)

    def persist(self, state_dir):
        from supervisor.state import save_risk_state

        save_risk_state(state_dir, self.state)

    def get_state(self):
        return self.state

    def apply_llm_advice(self, advice):
        if hasattr(advice, "risk_multiplier"):
            self.state.llm_risk_multiplier = advice.risk_multiplier
        if hasattr(advice, "action"):
            self.state.llm_last_action = str(advice.action)
