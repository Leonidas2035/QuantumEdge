"""Deterministic policy heuristics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from .signals import Signals


@dataclass
class HeuristicThresholds:
    max_daily_loss: Optional[float] = None
    max_drawdown_abs: Optional[float] = None
    loss_streak: int = 3
    spread_max_bps: Optional[float] = None
    volatility_hi: Optional[float] = None
    restart_rate: Optional[float] = None
    conservative_size_multiplier: float = 0.5
    loss_streak_mode: str = "conservative"  # conservative | risk_off


@dataclass
class HeuristicDecision:
    mode: str
    allow_trading: bool
    size_multiplier: float
    cooldown_sec: int
    spread_max_bps: Optional[float]
    max_daily_loss: Optional[float]
    reason: str
    evidence: str


def apply_heuristics(signals: Signals, thresholds: HeuristicThresholds) -> HeuristicDecision:
    evidence_parts: list[str] = []

    if not signals.bot_running:
        return HeuristicDecision(
            mode="risk_off",
            allow_trading=False,
            size_multiplier=0.0,
            cooldown_sec=0,
            spread_max_bps=thresholds.spread_max_bps,
            max_daily_loss=thresholds.max_daily_loss,
            reason="BOT_UNHEALTHY",
            evidence="bot_running=false",
        )

    if signals.risk_halted:
        return HeuristicDecision(
            mode="risk_off",
            allow_trading=False,
            size_multiplier=0.0,
            cooldown_sec=0,
            spread_max_bps=thresholds.spread_max_bps,
            max_daily_loss=thresholds.max_daily_loss,
            reason="RISK_ENGINE_HALTED",
            evidence=str(signals.risk_halt_reason or "risk_halted"),
        )

    if thresholds.restart_rate is not None and signals.restart_rate is not None:
        evidence_parts.append(f"restart_rate={signals.restart_rate:.2f}/h")
        if signals.restart_rate >= thresholds.restart_rate:
            return HeuristicDecision(
                mode="risk_off",
                allow_trading=False,
                size_multiplier=0.0,
                cooldown_sec=0,
                spread_max_bps=thresholds.spread_max_bps,
                max_daily_loss=thresholds.max_daily_loss,
                reason="BOT_RESTART_LOOP",
                evidence=";".join(evidence_parts),
            )

    if thresholds.max_daily_loss is not None and signals.pnl_day is not None:
        evidence_parts.append(f"pnl_day={signals.pnl_day:.2f}")
        if signals.pnl_day <= -abs(thresholds.max_daily_loss):
            return HeuristicDecision(
                mode="risk_off",
                allow_trading=False,
                size_multiplier=0.0,
                cooldown_sec=0,
                spread_max_bps=thresholds.spread_max_bps,
                max_daily_loss=thresholds.max_daily_loss,
                reason="DAILY_LOSS_LIMIT",
                evidence=";".join(evidence_parts),
            )

    if thresholds.max_drawdown_abs is not None and signals.drawdown_day is not None:
        evidence_parts.append(f"drawdown={signals.drawdown_day:.2f}")
        if signals.drawdown_day >= abs(thresholds.max_drawdown_abs):
            return HeuristicDecision(
                mode="risk_off",
                allow_trading=False,
                size_multiplier=0.0,
                cooldown_sec=0,
                spread_max_bps=thresholds.spread_max_bps,
                max_daily_loss=thresholds.max_daily_loss,
                reason="DRAWDOWN_LIMIT",
                evidence=";".join(evidence_parts),
            )

    if thresholds.loss_streak and signals.loss_streak is not None:
        evidence_parts.append(f"loss_streak={signals.loss_streak}")
        if signals.loss_streak >= thresholds.loss_streak:
            mode = "risk_off" if thresholds.loss_streak_mode == "risk_off" else "conservative"
            allow_trading = mode != "risk_off"
            size_multiplier = 0.0 if mode == "risk_off" else thresholds.conservative_size_multiplier
            return HeuristicDecision(
                mode=mode,
                allow_trading=allow_trading,
                size_multiplier=size_multiplier,
                cooldown_sec=0,
                spread_max_bps=thresholds.spread_max_bps,
                max_daily_loss=thresholds.max_daily_loss,
                reason="LOSS_STREAK",
                evidence=";".join(evidence_parts),
            )

    if thresholds.spread_max_bps is not None and signals.spread_bps is not None:
        evidence_parts.append(f"spread_bps={signals.spread_bps:.2f}")
        if signals.spread_bps >= thresholds.spread_max_bps:
            return HeuristicDecision(
                mode="risk_off",
                allow_trading=False,
                size_multiplier=0.0,
                cooldown_sec=0,
                spread_max_bps=thresholds.spread_max_bps,
                max_daily_loss=thresholds.max_daily_loss,
                reason="SPREAD_TOO_WIDE",
                evidence=";".join(evidence_parts),
            )

    if thresholds.volatility_hi is not None and signals.volatility is not None:
        evidence_parts.append(f"volatility={signals.volatility:.4f}")
        if signals.volatility >= thresholds.volatility_hi:
            return HeuristicDecision(
                mode="conservative",
                allow_trading=True,
                size_multiplier=thresholds.conservative_size_multiplier,
                cooldown_sec=0,
                spread_max_bps=thresholds.spread_max_bps,
                max_daily_loss=thresholds.max_daily_loss,
                reason="HIGH_VOL",
                evidence=";".join(evidence_parts),
            )

    return HeuristicDecision(
        mode="normal",
        allow_trading=True,
        size_multiplier=1.0,
        cooldown_sec=0,
        spread_max_bps=thresholds.spread_max_bps,
        max_daily_loss=thresholds.max_daily_loss,
        reason="OK",
        evidence=";".join(evidence_parts) if evidence_parts else "",
    )
