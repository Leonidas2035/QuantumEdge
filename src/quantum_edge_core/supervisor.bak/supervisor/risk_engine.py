"""Hard Risk Engine: Stateful implementation for supervisor."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Optional, Dict, Any, Union
from datetime import date

# Circular import prevention if needed, but RiskStateSnapshot is in supervisor.state
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


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
    quantity: Decimal
    price: Optional[Decimal] = None
    notional: Optional[Decimal] = None
    leverage: Optional[Decimal] = None
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

        # Coerce existing state values to Decimal if they are set (e.g. float from legacy code or tests)
        if getattr(self.state, "equity_start", None) is not None:
            self.state.equity_start = Decimal(str(self.state.equity_start))
        if getattr(self.state, "equity_now", None) is not None:
            self.state.equity_now = Decimal(str(self.state.equity_now))
        if getattr(self.state, "realized_pnl_today", None) is not None:
            self.state.realized_pnl_today = Decimal(str(self.state.realized_pnl_today))
        if getattr(self.state, "max_equity_intraday", None) is not None:
            self.state.max_equity_intraday = Decimal(
                str(self.state.max_equity_intraday)
            )
        if getattr(self.state, "min_equity_intraday", None) is not None:
            self.state.min_equity_intraday = Decimal(
                str(self.state.min_equity_intraday)
            )
        if getattr(self.state, "total_equity", None) is not None:
            self.state.total_equity = Decimal(str(self.state.total_equity))
        if getattr(self.state, "free_margin", None) is not None:
            self.state.free_margin = Decimal(str(self.state.free_margin))
        if getattr(self.state, "unrealized_pnl", None) is not None:
            self.state.unrealized_pnl = Decimal(str(self.state.unrealized_pnl))

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

        # Check for trading day rollover (midnight transition)
        today = date.today()
        if self.state.trading_day != today:
            if self.logger:
                self.logger.info(
                    "New trading day detected (%s -> %s). Rolling over risk state.",
                    self.state.trading_day,
                    today,
                )
            self.state.trading_day = today
            start_eq = (
                Decimal(str(equity)) if equity is not None else self.state.equity_now
            )
            self.state.equity_start = start_eq
            self.state.max_equity_intraday = start_eq
            self.state.min_equity_intraday = (
                start_eq if start_eq is not None else Decimal("0.0")
            )
            self.state.realized_pnl_today = Decimal("0.0")
            self.state.halted = False
            self.state.halt_reason = None

        if equity is not None:
            equity_val = Decimal(str(equity))
            # Detect sudden external changes (deposits/withdrawals)
            # If the equity changes by more than 5% compared to previous equity_now,
            # adjust equity_start and intraday max/min to avoid false halts.
            if (
                self.state.equity_now is not None
                and self.state.equity_start is not None
            ):
                prev_equity = self.state.equity_now
                diff = equity_val - prev_equity
                if abs(diff) > prev_equity * Decimal("0.05"):
                    if self.logger:
                        self.logger.info(
                            "Sudden equity change detected (%s -> %s, diff: %s). "
                            "Treating as external deposit/withdrawal. Adjusting equity_start.",
                            prev_equity,
                            equity_val,
                            diff,
                        )
                    self.state.equity_start += diff
                    if self.state.max_equity_intraday is not None:
                        self.state.max_equity_intraday += diff
                    if self.state.min_equity_intraday is not None:
                        self.state.min_equity_intraday += diff

            self.state.equity_now = equity_val
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
            self.state.realized_pnl_today = Decimal(str(realized_pnl))

        self.check_limits()

    def check_limits(self):
        """Check limits and halt if necessary."""
        if self.state.halted:
            return

        # Coerce to Decimal if they exist
        if getattr(self.state, "equity_start", None) is not None:
            self.state.equity_start = Decimal(str(self.state.equity_start))
        if getattr(self.state, "equity_now", None) is not None:
            self.state.equity_now = Decimal(str(self.state.equity_now))
        if getattr(self.state, "max_equity_intraday", None) is not None:
            self.state.max_equity_intraday = Decimal(
                str(self.state.max_equity_intraday)
            )
        if getattr(self.state, "min_equity_intraday", None) is not None:
            self.state.min_equity_intraday = Decimal(
                str(self.state.min_equity_intraday)
            )

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
        from hermes.supervisor.state import save_risk_state

        save_risk_state(state_dir, self.state)

    def get_state(self):
        return self.state

    def apply_llm_advice(self, advice):
        if hasattr(advice, "risk_multiplier"):
            self.state.llm_risk_multiplier = advice.risk_multiplier
        if hasattr(advice, "action"):
            self.state.llm_last_action = str(advice.action)
