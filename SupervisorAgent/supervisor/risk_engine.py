"""Global risk engine enforcing portfolio-wide limits."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from supervisor.config import RiskConfig, LlmSupervisorTrustPolicy
from supervisor.heartbeat import HeartbeatPayload
from supervisor import state as state_utils
from supervisor.llm_supervisor import LlmSupervisorAdvice, LlmAction

if TYPE_CHECKING:
    from supervisor.events import EventLogger


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


@dataclass
class OrderRequest:
    """Represents a normalized order request from QuantumEdge."""

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
    """Result of a risk evaluation."""

    allowed: bool
    code: str
    reason: str


class RiskEngine:
    """Evaluates orders and manages global risk state."""

    def __init__(
        self,
        limits: RiskConfig,
        state_snapshot: state_utils.RiskStateSnapshot,
        logger: Optional[logging.Logger] = None,
        event_logger: Optional["EventLogger"] = None,
        trust_policy: Optional[LlmSupervisorTrustPolicy] = None,
    ) -> None:
        self.limits = limits
        self.state = state_snapshot
        self.logger = logger or logging.getLogger(__name__)
        self._events = event_logger
        self._trust_policy = trust_policy

    def update_from_heartbeat(self, hb: HeartbeatPayload) -> None:
        """Refresh internal risk metrics based on the latest heartbeat."""

        if hb.trading_day and hb.trading_day != self.state.trading_day:
            self.logger.info("New trading day detected in heartbeat; resetting risk state.")
            self.state = state_utils.RiskStateSnapshot(
                trading_day=hb.trading_day,
                equity_start=None,
                equity_now=None,
                realized_pnl_today=None,
                max_equity_intraday=None,
                min_equity_intraday=None,
                halted=False,
                halt_reason=None,
            )

        if hb.equity is not None:
            if self.state.equity_start is None:
                self.state.equity_start = hb.equity
                self.state.max_equity_intraday = hb.equity
                self.state.min_equity_intraday = hb.equity
            self.state.equity_now = hb.equity
            self.state.max_equity_intraday = (
                max(self.state.max_equity_intraday, hb.equity) if self.state.max_equity_intraday is not None else hb.equity
            )
            self.state.min_equity_intraday = (
                min(self.state.min_equity_intraday, hb.equity) if self.state.min_equity_intraday is not None else hb.equity
            )

        if hb.realized_pnl_today is not None:
            self.state.realized_pnl_today = hb.realized_pnl_today

        self._evaluate_auto_halt()

    def _evaluate_auto_halt(self) -> None:
        """Set halt flag if any configured limit is breached."""

        equity_start = self.state.equity_start
        equity_now = self.state.equity_now
        if equity_start is None or equity_now is None:
            return

        daily_loss_abs = equity_start - equity_now
        drawdown_abs = None
        if self.state.max_equity_intraday is not None:
            drawdown_abs = self.state.max_equity_intraday - equity_now

        halt_reason: Optional[str] = None
        breach_code: Optional[str] = None
        was_halted = self.state.halted

        if self.limits.max_daily_loss_abs and daily_loss_abs >= self.limits.max_daily_loss_abs:
            halt_reason = f"Daily loss {daily_loss_abs:.2f} >= limit {self.limits.max_daily_loss_abs:.2f} {self.limits.currency}"
            breach_code = "DAILY_LOSS_LIMIT"

        if halt_reason is None and self.limits.max_daily_loss_pct and equity_start > 0:
            daily_loss_pct = daily_loss_abs / equity_start
            if daily_loss_pct >= self.limits.max_daily_loss_pct:
                halt_reason = f"Daily loss {daily_loss_pct:.2%} >= limit {self.limits.max_daily_loss_pct:.2%}"
                breach_code = "DAILY_LOSS_PCT"

        if halt_reason is None and drawdown_abs is not None and self.limits.max_drawdown_abs:
            if drawdown_abs >= self.limits.max_drawdown_abs:
                halt_reason = f"Drawdown {drawdown_abs:.2f} >= limit {self.limits.max_drawdown_abs:.2f} {self.limits.currency}"
                breach_code = "DD_LIMIT"

        if halt_reason is None and drawdown_abs is not None and self.limits.max_drawdown_pct and self.state.max_equity_intraday:
            if self.state.max_equity_intraday > 0:
                drawdown_pct = drawdown_abs / self.state.max_equity_intraday
                if drawdown_pct >= self.limits.max_drawdown_pct:
                    halt_reason = f"Drawdown {drawdown_pct:.2%} >= limit {self.limits.max_drawdown_pct:.2%}"
                    breach_code = "DD_PCT_LIMIT"

        if halt_reason:
            if not self.state.halted:
                self.logger.warning("Auto-halt triggered: %s", halt_reason)
            self.state.halted = True
            self.state.halt_reason = halt_reason
            if self._events and not was_halted and breach_code:
                self._events.log_risk_limit_breach(
                    breach_code,
                    {
                        "equity_start": equity_start,
                        "equity_now": equity_now,
                        "daily_loss_abs": daily_loss_abs,
                        "max_equity_intraday": self.state.max_equity_intraday,
                        "drawdown_abs": drawdown_abs,
                        "halt_reason": halt_reason,
                    },
                )

    def evaluate_order(self, order: OrderRequest) -> RiskDecision:
        """Evaluate an order request against configured limits."""

        if self.state.halted:
            if order.is_reduce_only:
                decision = RiskDecision(
                    allowed=True,
                    code="HALTED",
                    reason="Auto-halt active; only risk-reducing orders allowed.",
                )
            else:
                decision = RiskDecision(
                    allowed=False,
                    code="HALTED",
                    reason=self.state.halt_reason or "Trading halted by risk engine.",
                )
            if self._events:
                self._events.log_order_decision(order, decision)
            return decision

        if self.state.llm_paused:
            if order.is_reduce_only:
                decision = RiskDecision(
                    allowed=True,
                    code="LLM_PAUSE",
                    reason="LLM soft pause active; only risk-reducing orders allowed.",
                )
            else:
                decision = RiskDecision(
                    allowed=False,
                    code="LLM_PAUSE",
                    reason="LLM soft pause active; new trades blocked.",
                )
            if self._events:
                self._events.log_order_decision(order, decision)
            return decision

        leverage = order.leverage if order.leverage is not None else 1.0
        if leverage <= 0:
            decision = RiskDecision(False, "INVALID_ORDER", "Leverage must be positive.")
            if self._events:
                self._events.log_order_decision(order, decision)
            return decision

        multiplier = self.state.llm_risk_multiplier if self.state.llm_risk_multiplier else 1.0
        effective_max_notional = self.limits.max_notional_per_symbol * multiplier
        effective_max_leverage = self.limits.max_leverage * multiplier

        notional = order.notional
        if notional is None:
            if order.price is None:
                decision = RiskDecision(False, "INVALID_ORDER", "Notional or price must be provided.")
                if self._events:
                    self._events.log_order_decision(order, decision)
                return decision
            notional = order.price * order.quantity

        if notional < 0:
            decision = RiskDecision(False, "INVALID_ORDER", "Notional must be non-negative.")
            if self._events:
                self._events.log_order_decision(order, decision)
            return decision

        # TODO: Incorporate existing symbol exposure once integrated with QuantumEdge positions.
        if self.limits.max_notional_per_symbol and notional > effective_max_notional:
            decision = RiskDecision(
                allowed=False,
                code="SYMBOL_NOTIONAL_LIMIT",
                reason=f"Order notional {notional:.2f} exceeds per-symbol limit {effective_max_notional:.2f}.",
            )
            if self._events:
                self._events.log_order_decision(order, decision)
            return decision

        if self.limits.max_leverage and leverage > effective_max_leverage:
            decision = RiskDecision(
                allowed=False,
                code="LEVERAGE_LIMIT",
                reason=f"Order leverage {leverage:.2f} exceeds limit {effective_max_leverage:.2f}.",
            )
            if self._events:
                self._events.log_order_decision(order, decision)
            return decision

        decision = RiskDecision(True, "OK", "Order allowed.")
        if self._events:
            self._events.log_order_decision(order, decision)
        return decision

    def get_state(self) -> state_utils.RiskStateSnapshot:
        return self.state

    def persist(self, state_dir: Path) -> None:
        """Persist current risk snapshot to disk."""

        state_utils.save_risk_state(state_dir, self.state)

    def apply_llm_advice(self, advice: "LlmSupervisorAdvice") -> None:
        """Apply LLM advice according to trust policy."""

        self.state.llm_last_action = advice.action.value
        self.state.llm_last_reason = advice.comment

        if not self._trust_policy:
            return

        if advice.action == LlmAction.LOWER_RISK and advice.risk_multiplier is not None:
            if self._trust_policy.allow_risk_multiplier:
                new_mul = advice.risk_multiplier
                if self._trust_policy.min_multiplier <= new_mul <= self._trust_policy.max_multiplier:
                    if new_mul <= self.state.llm_risk_multiplier:
                        self.state.llm_risk_multiplier = new_mul

        if self._trust_policy.allow_pause:
            if advice.action == LlmAction.PAUSE:
                self.state.llm_paused = True
            elif advice.action == LlmAction.OK:
                self.state.llm_paused = False
