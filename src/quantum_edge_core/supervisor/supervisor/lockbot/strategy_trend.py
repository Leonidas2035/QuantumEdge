"""Trend unlock strategy (pullback-based)."""

from __future__ import annotations

from typing import Optional

from quantum_edge_core.supervisor.supervisor.lockbot.models import (
    BotStatusSnapshot,
    MarketSnapshot,
    PolicyIntent,
    StrategyDecision,
    TrendPolicyConfig,
)


def evaluate_trend(
    market: MarketSnapshot,
    status: BotStatusSnapshot,
    cfg: TrendPolicyConfig,
    direction: str,
) -> StrategyDecision:
    if market.mark_price is None:
        return StrategyDecision(intent=None, action="NOOP", reason="missing_price")
    ref_price, ref_source = _select_reference_price(market, cfg)
    if ref_price is None:
        return StrategyDecision(intent=None, action="NOOP", reason="missing_reference")
    if not _pullback_ok(market.mark_price, ref_price, cfg.pullback_bps, direction):
        return StrategyDecision(intent=None, action="NOOP", reason="no_pullback")
    if _heatmap_blocked(direction, market, cfg.heatmap_block):
        return StrategyDecision(intent=None, action="NOOP", reason="heatmap_block")
    if _funding_blocked(direction, market.funding_rate, cfg.funding_max_abs):
        return StrategyDecision(intent=None, action="NOOP", reason="funding_block")

    target = cfg.target_up if direction == "TREND_UP" else cfg.target_down
    delta_gap = target - status.net_delta
    action = _select_action(delta_gap, status)
    if action is None:
        return StrategyDecision(intent=None, action="NOOP", reason="delta_in_band")

    expected_edge_bps = abs(market.mark_price - ref_price) / ref_price * 10000.0
    if expected_edge_bps < cfg.min_edge_bps:
        return StrategyDecision(intent=None, action="NOOP", reason="edge_too_small")

    intent = PolicyIntent(
        cmd="EXEC_STEP",
        payload={
            "action": action,
            "qty_hint": cfg.step_qty_hint,
            "reason": f"trend_pullback:{ref_source}",
            "expected_edge_bps": round(expected_edge_bps, 3),
        },
        reason="trend_pullback",
        priority=70,
    )
    debug = {
        "direction": direction,
        "ref_source": ref_source,
        "ref_price": ref_price,
        "target": target,
        "delta_gap": delta_gap,
    }
    return StrategyDecision(
        intent=intent, action="EXEC_STEP", reason="trend_pullback", debug=debug
    )


def _select_reference_price(
    market: MarketSnapshot, cfg: TrendPolicyConfig
) -> tuple[Optional[float], str]:
    for anchor in cfg.avwap_anchor_preference:
        if anchor in market.avwap_anchors:
            return market.avwap_anchors[anchor], f"avwap:{anchor}"
    if market.avwap is not None:
        return market.avwap, "avwap"
    if market.vwap is not None:
        return market.vwap, "vwap"
    return None, "none"


def _pullback_ok(mark: float, ref: float, pullback_bps: float, direction: str) -> bool:
    if ref <= 0:
        return False
    delta_bps = (mark - ref) / ref * 10000.0
    if direction == "TREND_UP":
        return delta_bps <= pullback_bps
    return delta_bps >= -pullback_bps


def _select_action(delta_gap: float, status: BotStatusSnapshot) -> Optional[str]:
    if abs(delta_gap) < 1e-6:
        return None
    if delta_gap > 0:
        return "TRIM_SHORT" if status.short_qty > 0 else "ADD_LONG"
    return "TRIM_LONG" if status.long_qty > 0 else "ADD_SHORT"


def _heatmap_blocked(direction: str, market: MarketSnapshot, threshold: float) -> bool:
    if threshold <= 0:
        return False
    if direction == "TREND_UP":
        return market.liq.intensity_below >= threshold
    return market.liq.intensity_above >= threshold


def _funding_blocked(
    direction: str, funding_rate: Optional[float], max_abs: float
) -> bool:
    if funding_rate is None or max_abs <= 0:
        return False
    if direction == "TREND_UP" and funding_rate > max_abs:
        return True
    if direction == "TREND_DOWN" and funding_rate < -max_abs:
        return True
    return False
