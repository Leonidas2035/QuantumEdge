"""Range scalp policy using VWAP bands."""

from __future__ import annotations

from typing import Optional

from supervisor.lockbot.models import (
    BotStatusSnapshot,
    MarketSnapshot,
    PolicyIntent,
    RangePolicyConfig,
    StrategyDecision,
)


def evaluate_range(
    market: MarketSnapshot,
    status: BotStatusSnapshot,
    cfg: RangePolicyConfig,
) -> StrategyDecision:
    if market.mark_price is None or market.vwap is None:
        return StrategyDecision(intent=None, action="NOOP", reason="missing_price")
    if (
        market.band_1u is None
        or market.band_1l is None
        or market.band_2u is None
        or market.band_2l is None
    ):
        return StrategyDecision(intent=None, action="NOOP", reason="missing_bands")

    mark = market.mark_price
    if market.band_1l <= mark <= market.band_1u:
        return StrategyDecision(intent=None, action="NOOP", reason="inside_no_trade")
    bias = _range_bias(mark, market.band_2u, market.band_2l)
    if bias is None:
        return StrategyDecision(intent=None, action="NOOP", reason="inside_band2")

    if _heatmap_blocked(bias, market, cfg.heatmap_block):
        return StrategyDecision(intent=None, action="NOOP", reason="heatmap_block")
    if _funding_blocked(bias, market.funding_rate, cfg.funding_max_abs):
        return StrategyDecision(intent=None, action="NOOP", reason="funding_block")

    action = _select_action(bias, status)
    expected_edge_bps = abs(mark - market.vwap) / market.vwap * 10000.0
    if expected_edge_bps < cfg.min_edge_bps:
        return StrategyDecision(intent=None, action="NOOP", reason="edge_too_small")

    intent = PolicyIntent(
        cmd="EXEC_STEP",
        payload={
            "action": action,
            "qty_hint": cfg.step_qty_hint,
            "reason": "range_band2",
            "expected_edge_bps": round(expected_edge_bps, 3),
        },
        reason="range_band2",
        priority=60,
    )
    debug = {
        "bias": bias,
        "expected_edge_bps": expected_edge_bps,
        "mark": mark,
        "vwap": market.vwap,
    }
    return StrategyDecision(
        intent=intent, action="EXEC_STEP", reason="range_band2", debug=debug
    )


def _range_bias(mark: float, band_2u: float, band_2l: float) -> Optional[str]:
    if mark >= band_2u:
        return "SHORT"
    if mark <= band_2l:
        return "LONG"
    return None


def _select_action(bias: str, status: BotStatusSnapshot) -> str:
    if bias == "LONG":
        return "TRIM_SHORT" if status.short_qty > 0 else "ADD_LONG"
    return "TRIM_LONG" if status.long_qty > 0 else "ADD_SHORT"


def _heatmap_blocked(bias: str, market: MarketSnapshot, threshold: float) -> bool:
    if threshold <= 0:
        return False
    if bias == "LONG":
        return market.liq.intensity_below >= threshold
    return market.liq.intensity_above >= threshold


def _funding_blocked(bias: str, funding_rate: Optional[float], max_abs: float) -> bool:
    if funding_rate is None or max_abs <= 0:
        return False
    if bias == "LONG" and funding_rate > max_abs:
        return True
    if bias == "SHORT" and funding_rate < -max_abs:
        return True
    return False
