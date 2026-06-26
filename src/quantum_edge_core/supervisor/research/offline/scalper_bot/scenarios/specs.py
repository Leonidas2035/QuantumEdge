"""Scenario specifications for deterministic dataset slicing (S00-S24)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class ScenarioRule:
    metric: str
    op: str
    value: Any
    required: bool = True
    weight: float = 1.0

    def evaluate(self, metrics: Dict[str, Any]) -> Tuple[bool, float, Optional[str]]:
        val = metrics.get(self.metric)
        if val is None:
            return (
                (not self.required),
                0.0,
                f"missing:{self.metric}" if self.required else None,
            )
        ok = _apply_op(val, self.op, self.value)
        return (
            ok,
            self.weight if ok else 0.0,
            None if ok else f"{self.metric}:{self.op}:{self.value}",
        )


@dataclass
class ScenarioSpec:
    scenario_id: str
    name: str
    intent: str
    constraints: str
    rules: List[ScenarioRule]
    output_folder: str
    requires_depth: bool = False

    def evaluate(self, metrics: Dict[str, Any]) -> Tuple[bool, float, List[str]]:
        if self.requires_depth and not metrics.get("depth_available"):
            return False, 0.0, ["SKIP_DEPTH_REQUIRED"]
        score = 0.0
        reasons: List[str] = []
        for rule in self.rules:
            ok, weight, reason = rule.evaluate(metrics)
            if not ok and rule.required:
                if reason:
                    reasons.append(reason)
                return False, 0.0, reasons
            score += weight
        return True, score, reasons


def build_scenarios(thresholds: Dict[str, Any]) -> List[ScenarioSpec]:
    t = thresholds
    scenarios: List[ScenarioSpec] = [
        ScenarioSpec(
            "S00",
            "Strong uptrend, low pullbacks",
            "Directional uptrend with clean pullbacks.",
            "High slope + high fit, low alternation.",
            [
                ScenarioRule(
                    "trend_slope_bps_per_min", "gte", t["strong_trend_bps_per_min"]
                ),
                ScenarioRule("trend_r2", "gte", t["trend_r2_min"]),
                ScenarioRule("alternation_rate", "lte", t["alternation_low"]),
                ScenarioRule("vol_bps", "lte", t["vol_high_bps"], required=False),
            ],
            "S00",
        ),
        ScenarioSpec(
            "S01",
            "Strong downtrend, low pullbacks",
            "Directional downtrend with clean pullbacks.",
            "Negative slope + high fit, low alternation.",
            [
                ScenarioRule(
                    "trend_slope_bps_per_min", "lte", -t["strong_trend_bps_per_min"]
                ),
                ScenarioRule("trend_r2", "gte", t["trend_r2_min"]),
                ScenarioRule("alternation_rate", "lte", t["alternation_low"]),
                ScenarioRule("vol_bps", "lte", t["vol_high_bps"], required=False),
            ],
            "S01",
        ),
        ScenarioSpec(
            "S02",
            "Mild uptrend with pullbacks",
            "Uptrend but with frequent pullbacks.",
            "Moderate slope + higher alternation.",
            [
                ScenarioRule(
                    "trend_slope_bps_per_min",
                    "between",
                    [t["mild_trend_bps_per_min"], t["strong_trend_bps_per_min"]],
                ),
                ScenarioRule("trend_r2", "gte", t["mild_r2_min"]),
                ScenarioRule("alternation_rate", "gte", t["alternation_low"]),
            ],
            "S02",
        ),
        ScenarioSpec(
            "S03",
            "Mild downtrend with pullbacks",
            "Downtrend but with frequent pullbacks.",
            "Moderate negative slope + higher alternation.",
            [
                ScenarioRule(
                    "trend_slope_bps_per_min",
                    "between",
                    [-t["strong_trend_bps_per_min"], -t["mild_trend_bps_per_min"]],
                ),
                ScenarioRule("trend_r2", "gte", t["mild_r2_min"]),
                ScenarioRule("alternation_rate", "gte", t["alternation_low"]),
            ],
            "S03",
        ),
        ScenarioSpec(
            "S04",
            "Sideways range (tight)",
            "Low slope, tight range, low vol.",
            "Near-zero slope + narrow range.",
            [
                ScenarioRule(
                    "trend_slope_bps_per_min", "abs_lte", t["range_slope_bps_per_min"]
                ),
                ScenarioRule("range_bps", "lte", t["range_tight_bps"]),
                ScenarioRule("vol_bps", "lte", t["vol_low_bps"]),
            ],
            "S04",
        ),
        ScenarioSpec(
            "S05",
            "Sideways range (wide)",
            "Low slope, wider oscillations.",
            "Near-zero slope + wide range.",
            [
                ScenarioRule(
                    "trend_slope_bps_per_min", "abs_lte", t["range_slope_bps_per_min"]
                ),
                ScenarioRule("range_bps", "gte", t["range_wide_bps"]),
            ],
            "S05",
        ),
        ScenarioSpec(
            "S06",
            "Mean reversion (clear oscillation)",
            "Alternating returns with low trend.",
            "High alternation + low slope.",
            [
                ScenarioRule(
                    "trend_slope_bps_per_min", "abs_lte", t["range_slope_bps_per_min"]
                ),
                ScenarioRule("alternation_rate", "gte", t["alternation_high"]),
                ScenarioRule(
                    "range_bps", "between", [t["range_tight_bps"], t["range_wide_bps"]]
                ),
            ],
            "S06",
        ),
        ScenarioSpec(
            "S07",
            "Trend → range transition",
            "Trend early, range later.",
            "Slope decays over window.",
            [
                ScenarioRule(
                    "slope_first_bps_per_min", "gte", t["mild_trend_bps_per_min"]
                ),
                ScenarioRule(
                    "slope_last_bps_per_min", "abs_lte", t["range_slope_bps_per_min"]
                ),
                ScenarioRule("range_last_bps", "lte", t["range_tight_bps"]),
            ],
            "S07",
        ),
        ScenarioSpec(
            "S08",
            "Range → breakout (clean)",
            "Range first, then sustained breakout.",
            "Breakout flag with tight pre-range.",
            [
                ScenarioRule("range_first_bps", "lte", t["range_tight_bps"]),
                ScenarioRule("breakout", "is_true", True),
            ],
            "S08",
        ),
        ScenarioSpec(
            "S09",
            "Breakout fakeout",
            "Breakout then reversal past midpoint.",
            "Fakeout flag.",
            [
                ScenarioRule("fakeout", "is_true", True),
            ],
            "S09",
        ),
        ScenarioSpec(
            "S10",
            "Low volatility (flat micro-moves)",
            "Very low realized volatility.",
            "Low vol + tight range.",
            [
                ScenarioRule("vol_bps", "lte", t["vol_low_bps"]),
                ScenarioRule("range_bps", "lte", t["range_tight_bps"]),
            ],
            "S10",
        ),
        ScenarioSpec(
            "S11",
            "High volatility (rapid swings)",
            "High realized volatility.",
            "High vol + wide range.",
            [
                ScenarioRule("vol_bps", "gte", t["vol_high_bps"]),
                ScenarioRule("range_bps", "gte", t["range_wide_bps"], required=False),
            ],
            "S11",
        ),
        ScenarioSpec(
            "S12",
            "Volatility spike burst (short shock)",
            "Short spike inside window.",
            "Gap/vol spike detected.",
            [
                ScenarioRule("gap_bps_max", "gte", t["vol_spike_bps"]),
                ScenarioRule("vol_bps", "gte", t["vol_high_bps"], required=False),
            ],
            "S12",
        ),
        ScenarioSpec(
            "S13",
            "Post-spike decay (vol crush)",
            "High vol early, low vol late.",
            "Vol decays across window.",
            [
                ScenarioRule("vol_first_bps", "gte", t["vol_high_bps"]),
                ScenarioRule("vol_last_bps", "lte", t["vol_low_bps"]),
            ],
            "S13",
        ),
        ScenarioSpec(
            "S14",
            "Gap-like move in tick space",
            "Single large jump between ticks.",
            "Max gap exceeds threshold.",
            [
                ScenarioRule("gap_bps_max", "gte", t["gap_bps"]),
            ],
            "S14",
        ),
        ScenarioSpec(
            "S15",
            "Tight spread + deep book",
            "Ideal scalping depth with tight spread.",
            "Requires depth data.",
            [
                ScenarioRule("spread_bps_mean", "lte", t["spread_tight_bps"]),
                ScenarioRule("depth_usd_mean", "gte", t["depth_deep_usd"]),
            ],
            "S15",
            requires_depth=True,
        ),
        ScenarioSpec(
            "S16",
            "Tight spread + thin book",
            "Tight spread but shallow depth.",
            "Requires depth data.",
            [
                ScenarioRule("spread_bps_mean", "lte", t["spread_tight_bps"]),
                ScenarioRule("depth_usd_mean", "lte", t["depth_thin_usd"]),
            ],
            "S16",
            requires_depth=True,
        ),
        ScenarioSpec(
            "S17",
            "Wide spread + deep book",
            "Costly spreads even with depth.",
            "Requires depth data.",
            [
                ScenarioRule("spread_bps_mean", "gte", t["spread_wide_bps"]),
                ScenarioRule("depth_usd_mean", "gte", t["depth_deep_usd"]),
            ],
            "S17",
            requires_depth=True,
        ),
        ScenarioSpec(
            "S18",
            "Wide spread + thin book",
            "Avoid-trading microstructure.",
            "Requires depth data.",
            [
                ScenarioRule("spread_bps_mean", "gte", t["spread_wide_bps"]),
                ScenarioRule("depth_usd_mean", "lte", t["depth_thin_usd"]),
            ],
            "S18",
            requires_depth=True,
        ),
        ScenarioSpec(
            "S19",
            "Persistent buy imbalance",
            "Aggressive buy-side flow.",
            "High positive imbalance.",
            [
                ScenarioRule("imbalance", "gte", t["imbalance_strong"]),
            ],
            "S19",
        ),
        ScenarioSpec(
            "S20",
            "Persistent sell imbalance",
            "Aggressive sell-side flow.",
            "High negative imbalance.",
            [
                ScenarioRule("imbalance", "lte", -t["imbalance_strong"]),
            ],
            "S20",
        ),
        ScenarioSpec(
            "S21",
            "Rapid flip imbalance (chop)",
            "Imbalance near zero with high alternation.",
            "Alternation + low net imbalance.",
            [
                ScenarioRule("alternation_rate", "gte", t["alternation_high"]),
                ScenarioRule("imbalance", "abs_lte", t["imbalance_flip_max"]),
            ],
            "S21",
        ),
        ScenarioSpec(
            "S22",
            "Bursty ticks (irregular arrival)",
            "Micro-bursts in tick arrival.",
            "High burstiness + high tick rate.",
            [
                ScenarioRule("burstiness", "gte", t["burstiness_high"]),
                ScenarioRule("tick_rate", "gte", t["tick_rate_high"], required=False),
            ],
            "S22",
        ),
        ScenarioSpec(
            "S23",
            "Sparse ticks (illiquid-ish)",
            "Low tick rate.",
            "Sparse tick arrival.",
            [
                ScenarioRule("tick_rate", "lte", t["tick_rate_low"]),
            ],
            "S23",
        ),
        ScenarioSpec(
            "S24",
            "Noisy microstructure (whipsaw)",
            "High alternation with low trend.",
            "Choppy with frequent sign flips.",
            [
                ScenarioRule("alternation_rate", "gte", t["alternation_high"]),
                ScenarioRule(
                    "trend_slope_bps_per_min", "abs_lte", t["mild_trend_bps_per_min"]
                ),
                ScenarioRule("vol_bps", "gte", t["vol_low_bps"], required=False),
            ],
            "S24",
        ),
    ]
    return scenarios


def _apply_op(val: Any, op: str, target: Any) -> bool:
    try:
        if op == "gte":
            return float(val) >= float(target)
        if op == "lte":
            return float(val) <= float(target)
        if op == "gt":
            return float(val) > float(target)
        if op == "lt":
            return float(val) < float(target)
        if op == "between":
            low, high = float(target[0]), float(target[1])
            return low <= float(val) <= high
        if op == "abs_lte":
            return abs(float(val)) <= float(target)
        if op == "abs_gte":
            return abs(float(val)) >= float(target)
        if op == "is_true":
            return bool(val) is bool(target)
    except (TypeError, ValueError):
        return False
    return False
