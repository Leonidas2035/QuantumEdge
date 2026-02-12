"""DDN execution/safety decision engine."""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional

from LockBotBTC.lockbot_btc.ddn.config import DDNConfig, DDNProfile


@dataclass
class DDNIntent:
    action: str
    qty_hint: Optional[float] = None
    target: Optional[float] = None
    band_low: Optional[float] = None
    band_high: Optional[float] = None
    profile: Optional[str] = None
    expected_edge_bps: Optional[float] = None
    reason: Optional[str] = None
    urgency: Optional[str] = None


@dataclass
class DDNMarketSnapshot:
    mark_price: Optional[float]
    vwap_d: Optional[float]
    bands: Dict[str, Optional[float]]
    funding_rate: Optional[float]
    volatility_bps: Optional[float]
    market_lag_ms: Optional[int]


@dataclass
class DDNPositionSnapshot:
    long_qty: Optional[float]
    short_qty: Optional[float]
    margin_usage: Optional[float]
    distance_to_liq_bps: Optional[float]
    account_lag_ms: Optional[int]

    def net_delta(self) -> float:
        return (self.long_qty or 0.0) - (self.short_qty or 0.0)


@dataclass
class DDNContext:
    intent: DDNIntent
    market: DDNMarketSnapshot
    position: DDNPositionSnapshot
    profile: DDNProfile
    max_band_abs: float


@dataclass
class OrderPlan:
    side: str
    reduce_only: bool
    qty: float
    type: str
    limit_price: Optional[float]
    time_in_force: Optional[str]
    expected_cost_bps: float


@dataclass
class DDNDecision:
    verdict: str
    recommended_step_qty: Optional[float]
    order_plans: List[OrderPlan]
    reasons: List[str]
    expected_cost_bps: float
    adjusted_target: Optional[float] = None
    adjusted_band_low: Optional[float] = None
    adjusted_band_high: Optional[float] = None
    state_version_bump: bool = False


class DDNEngine:
    def __init__(self, cfg: DDNConfig) -> None:
        self._cfg = cfg
        self._action_ts: Deque[int] = deque()
        self._last_reject_ms: Optional[int] = None

    def evaluate(self, ctx: DDNContext, now_ms: Optional[int] = None) -> DDNDecision:
        now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
        intent = ctx.intent
        reasons: List[str] = []

        if _is_stale(ctx.market.market_lag_ms, ctx.position.account_lag_ms, self._cfg.panic_on_lag_ms):
            if intent.action not in {"PANIC_LOCK", "PAUSE"}:
                return self._reject(now_ms, ["STALE_DATA"])

        if _is_liq_risk(ctx.position.distance_to_liq_bps, self._cfg.min_distance_to_liq_bps) or intent.action == "PANIC_LOCK":
            return self._panic_decision(ctx, now_ms)

        if intent.action in {"PAUSE", "RESUME"}:
            return DDNDecision(
                verdict="ALLOW",
                recommended_step_qty=None,
                order_plans=[],
                reasons=["OK"],
                expected_cost_bps=0.0,
            )

        if self._is_rate_limited(now_ms):
            return self._reject(now_ms, ["RATE_LIMIT"])

        if self._is_in_cooldown(now_ms) and intent.action not in {"PANIC_LOCK", "PAUSE"}:
            return DDNDecision(
                verdict="REJECT",
                recommended_step_qty=None,
                order_plans=[],
                reasons=["COOLDOWN"],
                expected_cost_bps=0.0,
            )

        if ctx.position.margin_usage is not None and ctx.position.margin_usage > self._cfg.max_margin_usage:
            if intent.action in {"ADD_LONG", "ADD_SHORT"}:
                return self._reject(now_ms, ["MARGIN_CAP"])

        if intent.action in {"SET_REGIME", "SET_DELTA_TARGET"}:
            return self._accept_profile_change(ctx)

        step_qty = self._compute_step_qty(ctx)
        if step_qty is None:
            return self._reject(now_ms, ["MISSING_MARK_PRICE"])

        qty = self._choose_qty(intent, step_qty, ctx.market.mark_price)
        if qty is None:
            return self._reject(now_ms, ["INVALID_QTY"])

        adjusted_qty = self._clamp_to_band(ctx, qty, reasons)
        if adjusted_qty is None:
            return self._reject(now_ms, reasons or ["BAND_CLAMP"])
        if ctx.market.mark_price and adjusted_qty * ctx.market.mark_price < self._cfg.min_step_notional_usd:
            return self._reject(now_ms, ["MIN_STEP"])

        cost_bps = self._estimate_cost_bps(intent, ctx.market.funding_rate)
        if not self._cost_guard(intent.expected_edge_bps, cost_bps):
            return self._reject(now_ms, ["NEGATIVE_EDGE"], cost_bps)

        order_plan = self._order_plan_for_intent(intent.action, adjusted_qty, cost_bps)
        verdict = "ALLOW" if math.isclose(adjusted_qty, qty, rel_tol=1e-6) else "MODIFY"
        self._record_action(now_ms)
        return DDNDecision(
            verdict=verdict,
            recommended_step_qty=adjusted_qty,
            order_plans=[order_plan] if order_plan else [],
            reasons=reasons or ["OK"],
            expected_cost_bps=cost_bps,
        )

    def _panic_decision(self, ctx: DDNContext, now_ms: int) -> DDNDecision:
        delta = ctx.position.net_delta()
        if delta == 0:
            return DDNDecision(
                verdict="PANIC_ONLY",
                recommended_step_qty=0.0,
                order_plans=[],
                reasons=["PANIC_TRIGGERED", "ALREADY_HEDGED"],
                expected_cost_bps=0.0,
            )
        side = "SELL" if delta > 0 else "BUY"
        qty = min(abs(delta), self._max_step_qty(ctx))
        plan = OrderPlan(
            side=side,
            reduce_only=True,
            qty=qty,
            type="MARKET",
            limit_price=None,
            time_in_force=None,
            expected_cost_bps=self._estimate_cost_bps(DDNIntent(action="PANIC_LOCK"), ctx.market.funding_rate),
        )
        self._record_action(now_ms)
        return DDNDecision(
            verdict="PANIC_ONLY",
            recommended_step_qty=qty,
            order_plans=[plan],
            reasons=["PANIC_TRIGGERED"],
            expected_cost_bps=plan.expected_cost_bps,
        )

    def _accept_profile_change(self, ctx: DDNContext) -> DDNDecision:
        target = ctx.intent.target if ctx.intent.target is not None else ctx.profile.target
        band_low = ctx.intent.band_low if ctx.intent.band_low is not None else ctx.profile.band_low
        band_high = ctx.intent.band_high if ctx.intent.band_high is not None else ctx.profile.band_high
        band_low, band_high = _clamp_band(band_low, band_high, ctx.max_band_abs)
        modified = False
        if ctx.intent.band_low is not None and band_low != ctx.intent.band_low:
            modified = True
        if ctx.intent.band_high is not None and band_high != ctx.intent.band_high:
            modified = True
        return DDNDecision(
            verdict="MODIFY" if modified else "ALLOW",
            recommended_step_qty=None,
            order_plans=[],
            reasons=["OK"],
            expected_cost_bps=0.0,
            adjusted_target=target,
            adjusted_band_low=band_low,
            adjusted_band_high=band_high,
        )

    def _compute_step_qty(self, ctx: DDNContext) -> Optional[float]:
        mark = ctx.market.mark_price
        if not mark or mark <= 0:
            return None
        vol_bps = ctx.market.volatility_bps or 0.0
        vol_scale = min(vol_bps / 100.0, 1.0) * self._cfg.step_volatility_scale
        notional = self._cfg.min_step_notional_usd * (1.0 + vol_scale)
        notional = min(notional, self._cfg.max_step_notional_usd)
        return notional / mark

    def _max_step_qty(self, ctx: DDNContext) -> float:
        mark = ctx.market.mark_price or 1.0
        return self._cfg.max_step_notional_usd / mark

    def _choose_qty(self, intent: DDNIntent, step_qty: float, mark_price: Optional[float]) -> Optional[float]:
        if intent.qty_hint is None:
            return step_qty
        if intent.qty_hint <= 0:
            return None
        if not mark_price or mark_price <= 0:
            return None
        max_qty = self._cfg.max_step_notional_usd / mark_price
        return min(intent.qty_hint, max_qty)

    def _clamp_to_band(self, ctx: DDNContext, qty: float, reasons: List[str]) -> Optional[float]:
        delta = ctx.position.net_delta()
        target = ctx.profile.target
        band_low = ctx.profile.band_low
        band_high = ctx.profile.band_high
        band_low, band_high = _clamp_band(band_low, band_high, ctx.max_band_abs)

        action = ctx.intent.action
        next_delta = _apply_delta(delta, action, qty)
        if next_delta is None:
            return None
        if target + band_low <= next_delta <= target + band_high:
            return qty
        allowed_qty = _max_qty_within_band(delta, action, target + band_low, target + band_high)
        if allowed_qty is None or allowed_qty <= 0:
            reasons.append("BAND_CLAMP")
            return None
        reasons.append("BAND_CLAMP")
        return allowed_qty

    def _order_plan_for_intent(self, action: str, qty: float, cost_bps: float) -> Optional[OrderPlan]:
        side, reduce_only = _order_side(action)
        if side is None:
            return None
        order_type = "MARKET" if action == "PANIC_LOCK" else "LIMIT"
        return OrderPlan(
            side=side,
            reduce_only=reduce_only,
            qty=qty,
            type=order_type,
            limit_price=None,
            time_in_force=None,
            expected_cost_bps=cost_bps,
        )

    def _estimate_cost_bps(self, intent: DDNIntent, funding_rate: Optional[float]) -> float:
        order_type = "MARKET" if intent.action == "PANIC_LOCK" else "LIMIT"
        fee = self._cfg.taker_fee_bps if order_type == "MARKET" else self._cfg.maker_fee_bps
        slippage = self._cfg.expected_slippage_bps_market if order_type == "MARKET" else 0.0
        funding = abs(funding_rate or 0.0) * 10000.0 * self._cfg.funding_weight
        return fee + slippage + funding

    def _cost_guard(self, expected_edge_bps: Optional[float], cost_bps: float) -> bool:
        if expected_edge_bps is None:
            return cost_bps <= self._cfg.max_cost_bps_per_step
        return expected_edge_bps >= self._cfg.min_expected_edge_bps + cost_bps

    def _reject(self, now_ms: int, reasons: List[str], cost_bps: float = 0.0) -> DDNDecision:
        self._last_reject_ms = now_ms
        return DDNDecision(
            verdict="REJECT",
            recommended_step_qty=None,
            order_plans=[],
            reasons=reasons,
            expected_cost_bps=cost_bps,
        )

    def _record_action(self, now_ms: int) -> None:
        self._action_ts.append(now_ms)
        while self._action_ts and now_ms - self._action_ts[0] > 60_000:
            self._action_ts.popleft()

    def _is_rate_limited(self, now_ms: int) -> bool:
        while self._action_ts and now_ms - self._action_ts[0] > 60_000:
            self._action_ts.popleft()
        return len(self._action_ts) >= self._cfg.max_steps_per_minute

    def _is_in_cooldown(self, now_ms: int) -> bool:
        if self._last_reject_ms is None:
            return False
        return now_ms - self._last_reject_ms < self._cfg.cooldown_ms_after_reject


def _clamp_band(band_low: float, band_high: float, max_abs: float) -> tuple[float, float]:
    band_low = max(band_low, -max_abs)
    band_high = min(band_high, max_abs)
    if band_low > band_high:
        band_low, band_high = -max_abs, max_abs
    return band_low, band_high


def _apply_delta(delta: float, action: str, qty: float) -> Optional[float]:
    if action == "ADD_LONG":
        return delta + qty
    if action == "ADD_SHORT":
        return delta - qty
    if action == "TRIM_LONG":
        return delta - qty
    if action == "TRIM_SHORT":
        return delta + qty
    if action in {"SET_DELTA_TARGET", "SET_REGIME", "PAUSE", "RESUME", "EXIT_LOCK"}:
        return delta
    return None


def _max_qty_within_band(delta: float, action: str, band_low: float, band_high: float) -> Optional[float]:
    if action == "ADD_LONG":
        return max(0.0, band_high - delta)
    if action == "ADD_SHORT":
        return max(0.0, delta - band_low)
    if action == "TRIM_LONG":
        return max(0.0, delta - band_low)
    if action == "TRIM_SHORT":
        return max(0.0, band_high - delta)
    return None


def _order_side(action: str) -> tuple[Optional[str], bool]:
    if action == "ADD_LONG":
        return "BUY", False
    if action == "ADD_SHORT":
        return "SELL", False
    if action == "TRIM_LONG":
        return "SELL", True
    if action == "TRIM_SHORT":
        return "BUY", True
    return None, False


def _is_stale(market_lag_ms: Optional[int], account_lag_ms: Optional[int], limit_ms: int) -> bool:
    if market_lag_ms is None or account_lag_ms is None:
        return True
    return market_lag_ms > limit_ms or account_lag_ms > limit_ms


def _is_liq_risk(distance_bps: Optional[float], min_bps: float) -> bool:
    if distance_bps is None:
        return False
    return distance_bps < min_bps
