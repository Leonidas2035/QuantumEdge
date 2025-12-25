"""Policy engine combining heuristics + optional LLM moderation."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .circuit_breaker import CircuitBreaker
from .heuristics import HeuristicDecision, HeuristicThresholds, apply_heuristics
from .llm_moderator import LlmModerator
from .signals import Signals, collect_signals
from .policy_contract import Policy, POLICY_VERSION


@dataclass
class HysteresisConfig:
    enter_cycles: int = 2
    exit_cycles: int = 3


@dataclass
class PolicyEngineConfig:
    update_interval_sec: float
    ttl_sec: int
    cooldown_sec: int
    thresholds: HeuristicThresholds
    hysteresis: HysteresisConfig
    llm_enabled: bool
    llm_model: str
    llm_api_url: str
    llm_api_key_env: str
    llm_timeout_sec: float
    llm_temperature: float
    cb_failures: int
    cb_window_sec: int
    cb_open_sec: int
    policy_state_path: Optional[Path] = None


class PolicyHysteresis:
    def __init__(self, config: HysteresisConfig, state_path: Optional[Path] = None) -> None:
        self.config = config
        self.state_path = state_path
        self._danger_count = 0
        self._safe_count = 0
        self._mode = "normal"
        self._load_state()

    def _load_state(self) -> None:
        if not self.state_path or not self.state_path.exists():
            return
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return
        self._danger_count = int(data.get("danger_count", 0) or 0)
        self._safe_count = int(data.get("safe_count", 0) or 0)
        self._mode = str(data.get("mode", "normal"))

    def _persist(self) -> None:
        if not self.state_path:
            return
        payload = {
            "danger_count": self._danger_count,
            "safe_count": self._safe_count,
            "mode": self._mode,
            "updated_at": int(time.time()),
        }
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception:
            return

    def apply(self, decision: HeuristicDecision, immediate: bool) -> HeuristicDecision:
        if immediate:
            self._danger_count = 0
            self._safe_count = 0
            self._mode = decision.mode
            self._persist()
            return decision

        if self._mode == "risk_off":
            if decision.mode == "risk_off":
                self._safe_count = 0
                self._danger_count = 0
                self._persist()
                return decision
            self._safe_count += 1
            if self._safe_count >= max(1, self.config.exit_cycles):
                self._danger_count = 0
                self._mode = decision.mode
                self._persist()
                return decision
            hold = HeuristicDecision(
                mode="risk_off",
                allow_trading=False,
                size_multiplier=0.0,
                cooldown_sec=decision.cooldown_sec,
                spread_max_bps=decision.spread_max_bps,
                max_daily_loss=decision.max_daily_loss,
                reason="HYSTERESIS_HOLD",
                evidence=decision.evidence,
            )
            self._persist()
            return hold

        if decision.mode == "risk_off":
            self._danger_count += 1
            if self._danger_count >= max(1, self.config.enter_cycles):
                self._safe_count = 0
                self._mode = "risk_off"
                self._persist()
                return decision
            hold = HeuristicDecision(
                mode=self._mode,
                allow_trading=True,
                size_multiplier=decision.size_multiplier,
                cooldown_sec=decision.cooldown_sec,
                spread_max_bps=decision.spread_max_bps,
                max_daily_loss=decision.max_daily_loss,
                reason="HYSTERESIS_WAIT",
                evidence=decision.evidence,
            )
            self._persist()
            return hold

        self._danger_count = 0
        self._safe_count = 0
        self._mode = decision.mode
        self._persist()
        return decision


class PolicyEngine:
    def __init__(self, config: PolicyEngineConfig, paths, process_manager, risk_engine, logger) -> None:
        self.config = config
        self.paths = paths
        self.process_manager = process_manager
        self.risk_engine = risk_engine
        self.logger = logger
        self.hysteresis = PolicyHysteresis(config.hysteresis, config.policy_state_path)
        self.cb = CircuitBreaker(config.cb_failures, config.cb_window_sec, config.cb_open_sec)
        self.llm = None
        if config.llm_enabled:
            self.llm = LlmModerator(
                api_url=config.llm_api_url,
                api_key_env=config.llm_api_key_env,
                model=config.llm_model,
                timeout_sec=config.llm_timeout_sec,
                temperature=config.llm_temperature,
            )
        self._current_policy: Optional[Policy] = None
        self._last_signals: Optional[Signals] = None
        self._last_decision: Optional[HeuristicDecision] = None

    def _safe_policy(self, reason: str) -> Policy:
        return Policy(
            version=POLICY_VERSION,
            ts=int(time.time()),
            ttl_sec=self.config.ttl_sec,
            allow_trading=False,
            mode="risk_off",
            size_multiplier=0.0,
            cooldown_sec=self.config.cooldown_sec,
            spread_max_bps=self.config.thresholds.spread_max_bps,
            max_daily_loss=self.config.thresholds.max_daily_loss,
            reason=reason,
        )

    def _apply_llm(self, policy: Policy, signals: Signals) -> Policy:
        if not self.llm:
            return policy
        if not self.cb.allow():
            policy.reason = f"{policy.reason}|LLM_CB_OPEN"
            return policy
        try:
            overrides = self.llm.suggest(signals.to_dict(), policy.to_dict())
            if overrides:
                merged = policy.to_dict()
                merged.update(overrides)
                merged["version"] = POLICY_VERSION
                merged["ts"] = policy.ts
                merged["ttl_sec"] = policy.ttl_sec
                policy = Policy.from_dict(merged)
                if overrides.get("reason"):
                    policy.reason = str(overrides["reason"])
                else:
                    policy.reason = f"{policy.reason}|LLM_OK"
            self.cb.record_success()
        except Exception as exc:
            self.cb.record_failure()
            policy.reason = f"{policy.reason}|LLM_UNAVAILABLE"
            self.logger.warning("LLM policy moderation failed: %s", exc)
        return policy

    def evaluate(self) -> Policy:
        try:
            signals = collect_signals(self.paths, self.process_manager, self.risk_engine, self.logger)
            self._last_signals = signals
            decision = apply_heuristics(signals, self.config.thresholds)
            immediate = decision.reason in {"BOT_UNHEALTHY", "DAILY_LOSS_LIMIT", "DRAWDOWN_LIMIT", "RISK_ENGINE_HALTED"}
            decision = self.hysteresis.apply(decision, immediate=immediate)
            self._last_decision = decision
        except Exception as exc:
            self.logger.error("Policy heuristics failed: %s", exc)
            policy = self._safe_policy("HEURISTICS_ERROR")
            self._current_policy = policy
            return policy

        reason = decision.reason
        if decision.evidence:
            reason = f"{reason}:{decision.evidence}"

        policy = Policy(
            version=POLICY_VERSION,
            ts=int(time.time()),
            ttl_sec=self.config.ttl_sec,
            allow_trading=decision.allow_trading,
            mode=decision.mode,
            size_multiplier=decision.size_multiplier,
            cooldown_sec=self.config.cooldown_sec,
            spread_max_bps=decision.spread_max_bps,
            max_daily_loss=decision.max_daily_loss,
            reason=reason,
        )

        policy = self._apply_llm(policy, signals)
        self._current_policy = policy
        return policy

    def current_policy(self) -> Policy:
        if self._current_policy:
            return self._current_policy
        return self._safe_policy("POLICY_NOT_READY")

    def debug_payload(self) -> dict:
        return {
            "signals": self._last_signals.to_dict() if self._last_signals else None,
            "decision": self._last_decision.__dict__ if self._last_decision else None,
            "cb": self.cb.state(),
            "llm_enabled": bool(self.llm),
        }
