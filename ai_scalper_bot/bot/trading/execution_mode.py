"""Execution modes for trading decisions.

NormalExecutionMode preserves existing market execution behavior.
ScalpExecutionMode introduces additional risk-reducing gates and time-based exits.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from bot.engine.decision_types import DecisionAction
from bot.risk.scalp_guards import ScalpGuard
from bot.trading.order_policy import OrderPolicy
from bot.core.config_loader import config


@dataclass
class ExecutionResult:
    executed: bool
    reason: str
    size: float = 0.0
    action: Optional[str] = None
    skipped: bool = False


class NormalExecutionMode:
    """Wrapper around existing execution flow (market orders only)."""

    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger(__name__)
        tp_cfg = (config.get("risk", {}) or {}).get("tp_sl", {}) or {}
        self._base_stop_bps = float(tp_cfg.get("base_stop_bps", 40.0))
        self._tp_rr = float(tp_cfg.get("tp_rr", tp_cfg.get("rr", 1.5) or 1.5))

    async def execute_trade(
        self,
        decision,
        price: float,
        timestamp: int,
        symbol: str,
        trader,
        allow_fn,
        **_: Any,
    ) -> ExecutionResult:
        if decision.action == DecisionAction.ENTER:
            action = "buy" if decision.direction == "long" else "sell"
            sl_bps = max(10.0, self._base_stop_bps)
            tp_bps = sl_bps * self._tp_rr
            if decision.direction == "long":
                sl_price = price * (1 - sl_bps / 10_000)
                tp_price = price * (1 + tp_bps / 10_000)
            else:
                sl_price = price * (1 + sl_bps / 10_000)
                tp_price = price * (1 - tp_bps / 10_000)
            if await allow_fn(action, decision.size, False):
                trader_decision = type(
                    "Tmp",
                    (),
                    {"action": action, "size": decision.size, "order_type": "market", "tp_price": tp_price, "sl_price": sl_price},
                )
                await trader.process(trader_decision, price, timestamp, symbol=symbol)
                if hasattr(trader, "set_bracket"):
                    trader.set_bracket(action, tp_price, sl_price)
                return ExecutionResult(executed=True, reason="enter", size=decision.size or 0.0, action=action)
            return ExecutionResult(executed=False, reason="supervisor_block", skipped=True)

        if decision.action == DecisionAction.EXIT:
            if await allow_fn("close", abs(trader.position), True):
                trader_decision = type("Tmp", (), {"action": "close", "size": 0, "order_type": "market"})
                await trader.process(trader_decision, price, timestamp, symbol=symbol)
                return ExecutionResult(executed=True, reason="exit", size=abs(trader.position), action="close")
            return ExecutionResult(executed=False, reason="supervisor_block", skipped=True)

        return ExecutionResult(executed=False, reason="noop", skipped=True)

    async def enforce_time_stop(self, *_, **__) -> None:
        """No-op for normal execution."""
        return None


class ScalpExecutionMode:
    """Execution mode with additional scalp gates and time-based exits."""

    def __init__(
        self,
        scalp_cfg: Dict[str, Any],
        guard: ScalpGuard,
        order_policy: OrderPolicy,
        logger: Optional[logging.Logger] = None,
    ):
        self.cfg = scalp_cfg or {}
        self.guard = guard
        self.policy = order_policy
        self.logger = logger or logging.getLogger(__name__)
        self.entry_times: Dict[str, float] = {}

        self.min_prob_up = float(self.cfg.get("min_prob_up", 0.55))
        self.min_edge = float(self.cfg.get("min_edge", 0.0))
        self.max_spread_bps = float(self.cfg.get("max_spread_bps", 2.0))
        self.min_depth_usd = float(self.cfg.get("min_orderbook_depth_usd", self.cfg.get("min_depth_quote", 1000.0) or 1000.0))
        self.max_hold_seconds = int(self.cfg.get("max_position_hold_seconds", 60))
        self._disable_if_no_depth = bool(self.cfg.get("disable_without_depth", True))

    @staticmethod
    def _bps(val: float) -> float:
        return val * 10_000

    def _spread_bps(self, last_event: Optional[Dict[str, Any]], price: float) -> float:
        # No full orderbook in this environment; fallback to 0 bps.
        if not last_event:
            return 0.0
        bid = float(last_event.get("b", 0.0) or 0.0)
        ask = float(last_event.get("a", 0.0) or 0.0)
        if bid <= 0 or ask <= 0 or ask <= bid:
            return 0.0
        spread = ask - bid
        return self._bps(spread / ((ask + bid) / 2))

    def _depth_usd(self, last_event: Optional[Dict[str, Any]], price: float, qty: float) -> float:
        # Approximate depth using last trade size if no orderbook is available.
        if not last_event:
            return price * qty
        depth = last_event.get("depth") or price * qty
        try:
            return float(depth)
        except Exception:
            return price * qty

    def _compute_stop_targets(self, price: float, direction: str) -> Dict[str, float]:
        min_sl = float(self.cfg.get("min_stop_distance_bps", 2.0))
        max_sl = float(self.cfg.get("max_stop_distance_bps", 15.0))
        sl_rr = float(self.cfg.get("sl_rr", 0.6))
        tp_rr = float(self.cfg.get("tp_rr", 0.4))

        sl_bps = min(max_sl, max(min_sl, min_sl))
        if sl_rr > 0:
            tp_bps = sl_bps * (tp_rr / sl_rr)
        else:
            tp_bps = sl_bps

        if direction == "long":
            sl_price = price * (1 - sl_bps / 10_000)
            tp_price = price * (1 + tp_bps / 10_000)
        else:
            sl_price = price * (1 + sl_bps / 10_000)
            tp_price = price * (1 - tp_bps / 10_000)
        return {"sl_price": sl_price, "tp_price": tp_price, "sl_bps": sl_bps, "tp_bps": tp_bps}

    async def execute_trade(
        self,
        decision,
        price: float,
        timestamp: int,
        symbol: str,
        trader,
        allow_fn,
        signal: Optional[Any] = None,
        last_event: Optional[Dict[str, Any]] = None,
        **_: Any,
    ) -> ExecutionResult:
        # Exit handling remains straightforward; apply guard bookkeeping.
        if decision.action == DecisionAction.EXIT:
            if await allow_fn("close", abs(trader.position), True):
                await self.policy.close_position(trader, abs(trader.position), price, timestamp, symbol=symbol)
                self.guard.record_exit()
                self.entry_times.pop(symbol, None)
                return ExecutionResult(executed=True, reason="exit", action="close", size=abs(trader.position))
            return ExecutionResult(executed=False, reason="supervisor_block", skipped=True)

        if decision.action != DecisionAction.ENTER:
            return ExecutionResult(executed=False, reason="noop", skipped=True)

        ok, guard_reason = self.guard.can_enter()
        if not ok:
            self.logger.info("Scalp entry blocked by guard: %s", guard_reason)
            return ExecutionResult(executed=False, reason=guard_reason, skipped=True)

        # Signal quality checks
        prob_ok = True
        edge_ok = True
        if signal is not None:
            if decision.direction == "long":
                prob_ok = bool(getattr(signal, "p_up", 0.0) >= self.min_prob_up)
            else:
                prob_ok = bool(getattr(signal, "p_down", 0.0) >= self.min_prob_up)
            edge_ok = bool(abs(getattr(signal, "edge", 0.0)) >= self.min_edge)
        if not prob_ok:
            return ExecutionResult(executed=False, reason="probability_below_threshold", skipped=True)
        if not edge_ok:
            return ExecutionResult(executed=False, reason="edge_below_threshold", skipped=True)

        spread_bps = self._spread_bps(last_event, price)
        if spread_bps > self.max_spread_bps:
            return ExecutionResult(executed=False, reason="spread_too_wide", skipped=True)

        qty = float(last_event.get("q", 0.0)) if last_event else 0.0
        depth_usd = self._depth_usd(last_event, price, qty)
        if depth_usd < self.min_depth_usd:
            if self._disable_if_no_depth:
                self.logger.warning("Scalp disabled due to insufficient depth (%.2f < %.2f).", depth_usd, self.min_depth_usd)
                return ExecutionResult(executed=False, reason="insufficient_depth", skipped=True)
            return ExecutionResult(executed=False, reason="insufficient_depth", skipped=True)

        action = "buy" if decision.direction == "long" else "sell"
        size = decision.size
        if await allow_fn(action, size, False):
            stops = self._compute_stop_targets(price, decision.direction)
            self.logger.debug(
                "Scalp stops for %s: SL@%.5f (%.2fbps) TP@%.5f (%.2fbps)",
                symbol,
                stops["sl_price"],
                stops["sl_bps"],
                stops["tp_price"],
                stops["tp_bps"],
            )
            await self.policy.place_scalp_order(trader, action, size, price, timestamp, symbol, tp_price=stops["tp_price"], sl_price=stops["sl_price"])
            if hasattr(trader, "set_bracket"):
                trader.set_bracket(action, stops["tp_price"], stops["sl_price"])
            self.guard.record_entry()
            self.entry_times[symbol] = timestamp / 1000.0
            return ExecutionResult(executed=True, reason="enter", action=action, size=size)
        return ExecutionResult(executed=False, reason="supervisor_block", skipped=True)

    async def enforce_time_stop(
        self,
        trader,
        price: float,
        timestamp: int,
        symbol: str,
        allow_fn=None,
    ) -> Optional[ExecutionResult]:
        if symbol not in self.entry_times:
            return None
        entry_ts = self.entry_times.get(symbol)
        if entry_ts is None:
            return None
        now_s = timestamp / 1000.0
        if now_s - entry_ts < self.max_hold_seconds:
            return None

        if allow_fn is None or await allow_fn("close", abs(trader.position), True):
            await self.policy.close_position(trader, abs(trader.position), price, timestamp, symbol=symbol)
            self.guard.record_exit()
            self.entry_times.pop(symbol, None)
            self.logger.info("Closed scalp position due to max hold time for %s", symbol)
            return ExecutionResult(executed=True, reason="time_stop", action="close", size=abs(trader.position))
        return ExecutionResult(executed=False, reason="supervisor_block_time_stop", skipped=True)
