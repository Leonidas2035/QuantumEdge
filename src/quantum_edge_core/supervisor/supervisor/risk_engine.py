"""Hard Risk Engine: Pure logic for critical risk limits."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, TYPE_CHECKING

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


class HardRiskEngine:
    """
    Stateful risk engine.
    """

    def __init__(self, config, state: RiskStateSnapshot, logger, event_logger, trust_policy):
        self.config = config
        self.state = state
        self.logger = logger or logging.getLogger(__name__)
        self.events = event_logger
        self.trust_policy = trust_policy

    def update_from_heartbeat(self, payload):
        """Updates internal risk state from a heartbeat payload."""
        if hasattr(payload, 'equity') and payload.equity is not None:
            self.state.equity_now = payload.equity
            if self.state.equity_start is None:
                self.state.equity_start = payload.equity

        if hasattr(payload, 'realized_pnl_today') and payload.realized_pnl_today is not None:
            self.state.realized_pnl_today = payload.realized_pnl_today

        if hasattr(payload, 'unrealized_pnl') and payload.unrealized_pnl is not None:
            self.state.unrealized_pnl = payload.unrealized_pnl

        # Update intraday max/min
        if self.state.equity_now is not None:
            if self.state.max_equity_intraday is None or self.state.equity_now > self.state.max_equity_intraday:
                self.state.max_equity_intraday = self.state.equity_now
            if self.state.min_equity_intraday is None or self.state.equity_now < self.state.min_equity_intraday:
                self.state.min_equity_intraday = self.state.equity_now
        
        self._check_limits()

    def _check_limits(self):
        """Internal check of risk limits to potentially trigger a halt."""
        if self.state.halted:
            return

        # 1. Check Max Daily Loss
        if self.config.max_daily_loss_abs is not None and self.state.equity_start is not None and self.state.equity_now is not None:
            daily_loss = self.state.equity_start - self.state.equity_now
            if daily_loss > self.config.max_daily_loss_abs:
                self.state.halted = True
                self.state.halt_reason = f"Max Daily Loss exceeded: {daily_loss:.2f} > {self.config.max_daily_loss_abs:.2f}"
                self.logger.critical(self.state.halt_reason)
                return

        # 2. Check Max Drawdown
        if self.config.max_drawdown_abs is not None and self.state.max_equity_intraday is not None and self.state.equity_now is not None:
            drawdown = self.state.max_equity_intraday - self.state.equity_now
            if drawdown > self.config.max_drawdown_abs:
                self.state.halted = True
                self.state.halt_reason = f"Max Drawdown exceeded: {drawdown:.2f} > {self.config.max_drawdown_abs:.2f}"
                self.logger.critical(self.state.halt_reason)
                return

    def evaluate_order(self, order: OrderRequest) -> RiskDecision:
        """Evaluates an order request against risk limits."""
        if self.state.halted:
            if order.is_reduce_only:
                return RiskDecision(True, "HALTED_REDUCE_ONLY", "Halted but allow reduce-only")
            return RiskDecision(False, "HALTED", f"Halted: {self.state.halt_reason}")

        # Basic leverage check if configured
        if getattr(self.config, 'max_leverage', None) and order.leverage and order.leverage > self.config.max_leverage:
             return RiskDecision(False, "MAX_LEVERAGE", f"Requested leverage {order.leverage} > {self.config.max_leverage}")

        return RiskDecision(True, "OK", "OK")

    def persist(self, state_dir: Path):
        """Persists risk state to disk."""
        from supervisor.state import save_risk_state
        save_risk_state(state_dir, self.state)

    def get_state(self) -> RiskStateSnapshot:
        """Returns the current risk state."""
        return self.state

    def apply_llm_advice(self, advice):
        """Applies advice from LLM supervisor."""
        self.logger.info("Applying LLM advice: %s", advice)
        if not self.trust_policy:
            return

        if getattr(self.trust_policy, 'allow_risk_multiplier', False) and hasattr(advice, 'risk_multiplier'):
            self.state.llm_risk_multiplier = max(
                getattr(self.trust_policy, 'min_multiplier', 0.1),
                min(getattr(self.trust_policy, 'max_multiplier', 1.0), advice.risk_multiplier)
            )

        if getattr(self.trust_policy, 'allow_pause', False) and hasattr(advice, 'action') and advice.action == "PAUSE":
            self.state.llm_paused = True
            self.state.llm_last_action = "PAUSE"

        if hasattr(advice, 'comment'):
            self.state.llm_last_reason = advice.comment
