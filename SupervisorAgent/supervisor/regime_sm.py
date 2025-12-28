"""Regime state machine with hysteresis."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

import yaml


@dataclass
class RegimeConfig:
    enter_confirm_cycles: int = 2
    exit_confirm_cycles: int = 2
    cooldown_sec: int = 10
    trend_score_threshold: Optional[float] = None
    volatility_panic: Optional[float] = None
    volatility_recover: Optional[float] = None
    spread_bps_panic: Optional[float] = None
    spread_bps_recover: Optional[float] = None
    unwind_window_sec: int = 120


@dataclass
class DirectivesConfig:
    update_interval_s: int = 10


@dataclass
class RegimeDecision:
    current_state: str
    proposed_state: Optional[str]
    reason_codes: list[str]
    scores: Dict[str, Optional[float]]
    since_ts: float
    cooldown_remaining_s: float
    changed: bool
    blocked_reason: Optional[str] = None


def load_regime_config(path: Path) -> RegimeConfig:
    if not path.exists():
        return RegimeConfig()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    section = raw.get("regime_sm", {}) or {}
    return RegimeConfig(
        enter_confirm_cycles=int(section.get("enter_confirm_cycles", 2)),
        exit_confirm_cycles=int(section.get("exit_confirm_cycles", 2)),
        cooldown_sec=int(section.get("cooldown_sec", 10)),
        trend_score_threshold=_coerce_optional_float(section.get("trend_score_threshold")),
        volatility_panic=_coerce_optional_float(section.get("volatility_panic")),
        volatility_recover=_coerce_optional_float(section.get("volatility_recover")),
        spread_bps_panic=_coerce_optional_float(section.get("spread_bps_panic")),
        spread_bps_recover=_coerce_optional_float(section.get("spread_bps_recover")),
        unwind_window_sec=int(section.get("unwind_window_sec", 120)),
    )


def load_directives_config(path: Path) -> DirectivesConfig:
    if not path.exists():
        return DirectivesConfig()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    section = raw.get("directives", {}) or {}
    return DirectivesConfig(update_interval_s=int(section.get("update_interval_s", 10)))


def _coerce_optional_float(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class RegimeStateMachine:
    def __init__(self, cfg: RegimeConfig) -> None:
        self.cfg = cfg
        self.state = "RANGE"
        now = time.time()
        self.since_ts = now
        self.cooldown_until = 0.0
        self._pending_state: Optional[str] = None
        self._pending_count = 0

    def evaluate(self, signals: Dict[str, Optional[float]], guard_critical: bool) -> RegimeDecision:
        now = time.time()
        reason_codes: list[str] = []
        scores = {
            "trend_score": signals.get("trend_score"),
            "volatility": signals.get("volatility"),
            "spread_bps": signals.get("spread_bps"),
        }
        target = self.state
        if guard_critical:
            target = "FREEZE"
            reason_codes.append("GUARD_CRITICAL")
        elif self.state == "FREEZE":
            target = "UNWIND"
            reason_codes.append("SAFE_TO_UNWIND")
        elif self.state == "UNWIND":
            if (now - self.since_ts) >= self.cfg.unwind_window_sec:
                target = "RANGE"
                reason_codes.append("UNWIND_WINDOW_END")
        else:
            if _panic_condition(signals, self.cfg):
                target = "PANIC"
                reason_codes.append("PANIC_SIGNAL")
            elif _trend_condition(signals, self.cfg):
                target = "TREND"
                reason_codes.append("TREND_SIGNAL")
            else:
                target = "RANGE"
                reason_codes.append("RANGE_DEFAULT")

        proposed_state = target if target != self.state else None
        cooldown_remaining = max(self.cooldown_until - now, 0.0)
        blocked_reason = None
        changed = False

        if proposed_state and cooldown_remaining > 0:
            blocked_reason = "COOLDOWN_ACTIVE"
        elif proposed_state:
            confirm_needed = self.cfg.enter_confirm_cycles
            if self.state in {"PANIC", "FREEZE", "UNWIND"} and proposed_state == "RANGE":
                confirm_needed = self.cfg.exit_confirm_cycles
            if proposed_state == self._pending_state:
                self._pending_count += 1
            else:
                self._pending_state = proposed_state
                self._pending_count = 1

            if self._pending_count >= max(1, confirm_needed):
                self.state = proposed_state
                self.since_ts = now
                self.cooldown_until = now + max(0, self.cfg.cooldown_sec)
                self._pending_state = None
                self._pending_count = 0
                changed = True
            else:
                blocked_reason = "HYSTERESIS_CONFIRM"

        return RegimeDecision(
            current_state=self.state,
            proposed_state=proposed_state,
            reason_codes=reason_codes,
            scores=scores,
            since_ts=self.since_ts,
            cooldown_remaining_s=cooldown_remaining,
            changed=changed,
            blocked_reason=blocked_reason,
        )


def _panic_condition(signals: Dict[str, Optional[float]], cfg: RegimeConfig) -> bool:
    vol = signals.get("volatility")
    spread = signals.get("spread_bps")
    if cfg.volatility_panic is not None and vol is not None and vol >= cfg.volatility_panic:
        return True
    if cfg.spread_bps_panic is not None and spread is not None and spread >= cfg.spread_bps_panic:
        return True
    return False


def _trend_condition(signals: Dict[str, Optional[float]], cfg: RegimeConfig) -> bool:
    score = signals.get("trend_score")
    if cfg.trend_score_threshold is not None and score is not None:
        return score >= cfg.trend_score_threshold
    return False
