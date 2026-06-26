"""Guard evaluation for Supervisor decisions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

import yaml


@dataclass
class GuardConfig:
    spread_bps_max: Optional[float] = None
    depth_usd_min: Optional[float] = None
    max_margin_used_pct: Optional[float] = None
    min_liq_distance_pct: Optional[float] = None
    max_drawdown_pct: Optional[float] = None
    max_loss_streak: Optional[int] = None
    max_trades_per_hour: Optional[int] = None


@dataclass
class GuardResult:
    allowed: bool
    blocked_actions: list[str]
    reason_codes: list[str]
    details: Dict[str, Optional[float]]
    critical: bool = False

    def to_dict(self) -> Dict[str, object]:
        return {
            "allowed": self.allowed,
            "blocked_actions": self.blocked_actions,
            "reason_codes": self.reason_codes,
            "details": self.details,
            "critical": self.critical,
        }


def load_guard_config(path: Path) -> GuardConfig:
    if not path.exists():
        return GuardConfig()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    section = raw.get("guards", {}) or {}
    return GuardConfig(
        spread_bps_max=_coerce_optional_float(section.get("spread_bps_max")),
        depth_usd_min=_coerce_optional_float(section.get("depth_usd_min")),
        max_margin_used_pct=_coerce_optional_float(section.get("max_margin_used_pct")),
        min_liq_distance_pct=_coerce_optional_float(
            section.get("min_liq_distance_pct")
        ),
        max_drawdown_pct=_coerce_optional_float(section.get("max_drawdown_pct")),
        max_loss_streak=_coerce_optional_int(section.get("max_loss_streak")),
        max_trades_per_hour=_coerce_optional_int(section.get("max_trades_per_hour")),
    )


def _coerce_optional_float(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_optional_int(value) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class GuardEvaluator:
    def __init__(self, cfg: GuardConfig) -> None:
        self.cfg = cfg

    def evaluate(self, context: Dict[str, Optional[float]]) -> GuardResult:
        reasons: list[str] = []
        blocked_actions: list[str] = []
        critical = False

        spread_bps = context.get("spread_bps")
        if (
            self.cfg.spread_bps_max is not None
            and spread_bps is not None
            and spread_bps > self.cfg.spread_bps_max
        ):
            reasons.append("SPREAD_TOO_WIDE")

        depth_usd = context.get("depth_usd")
        if (
            self.cfg.depth_usd_min is not None
            and depth_usd is not None
            and depth_usd < self.cfg.depth_usd_min
        ):
            reasons.append("DEPTH_TOO_LOW")

        margin_used_pct = context.get("margin_used_pct")
        if (
            self.cfg.max_margin_used_pct is not None
            and margin_used_pct is not None
            and margin_used_pct > self.cfg.max_margin_used_pct
        ):
            reasons.append("MAX_MARGIN_USED_PCT")
            critical = True

        liq_distance_pct = context.get("liq_distance_pct")
        if (
            self.cfg.min_liq_distance_pct is not None
            and liq_distance_pct is not None
            and liq_distance_pct < self.cfg.min_liq_distance_pct
        ):
            reasons.append("MIN_LIQ_DISTANCE_PCT")
            critical = True

        drawdown_pct = context.get("drawdown_pct")
        if (
            self.cfg.max_drawdown_pct is not None
            and drawdown_pct is not None
            and drawdown_pct > self.cfg.max_drawdown_pct
        ):
            reasons.append("MAX_DRAWDOWN_PCT")
            critical = True

        loss_streak = context.get("loss_streak")
        if (
            self.cfg.max_loss_streak is not None
            and loss_streak is not None
            and loss_streak >= self.cfg.max_loss_streak
        ):
            reasons.append("MAX_LOSS_STREAK")

        trades_per_hour = context.get("trades_per_hour")
        if (
            self.cfg.max_trades_per_hour is not None
            and trades_per_hour is not None
            and trades_per_hour >= self.cfg.max_trades_per_hour
        ):
            reasons.append("MAX_TRADES_PER_HOUR")

        if reasons:
            blocked_actions = ["ENTER_TRADE"]

        details = {
            "spread_bps": spread_bps,
            "depth_usd": depth_usd,
            "margin_used_pct": margin_used_pct,
            "liq_distance_pct": liq_distance_pct,
            "drawdown_pct": drawdown_pct,
            "loss_streak": loss_streak,
            "trades_per_hour": trades_per_hour,
        }

        return GuardResult(
            allowed=not bool(reasons),
            blocked_actions=blocked_actions,
            reason_codes=reasons,
            details=details,
            critical=critical,
        )
