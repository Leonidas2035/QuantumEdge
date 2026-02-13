"""Autopilot state machine and controller."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from quantum_edge_core.supervisor.supervisor.autopilot.audit import AuditLogger
from quantum_edge_core.supervisor.supervisor.autopilot.collector import MetricsCollector, MetricsSnapshot
from quantum_edge_core.supervisor.supervisor.autopilot.quality import QualityMonitor, QualityIssue
from quantum_edge_core.supervisor.supervisor.autopilot.remediation import RemediationManager
from quantum_edge_core.supervisor.supervisor.autopilot.policy_manager import PolicyManager


STATES = {"OFF", "SHADOW", "LIVE_DEMO", "LIVE", "DEGRADED", "HALTED"}


@dataclass
class AutopilotState:
    state: str
    last_transition_ts: float
    transitions: List[float] = field(default_factory=list)


class AutopilotStateMachine:
    def __init__(self, allowed_states: List[str], min_dwell_sec: int, max_transitions_per_hour: int) -> None:
        self.allowed_states = [s for s in allowed_states if s in STATES]
        self.min_dwell_sec = max(int(min_dwell_sec), 0)
        self.max_transitions_per_hour = max(int(max_transitions_per_hour), 1)

    def can_transition(self, state: AutopilotState, now: float) -> bool:
        if now - state.last_transition_ts < self.min_dwell_sec:
            return False
        recent = [t for t in state.transitions if now - t < 3600]
        return len(recent) < self.max_transitions_per_hour

    def next_state(self, state: AutopilotState, desired: str, now: float) -> AutopilotState:
        desired = desired if desired in self.allowed_states else state.state
        if desired == state.state:
            return state
        if not self.can_transition(state, now):
            return state
        transitions = [t for t in state.transitions if now - t < 3600]
        transitions.append(now)
        return AutopilotState(state=desired, last_transition_ts=now, transitions=transitions)


class AutopilotController:
    def __init__(
        self,
        state_path: Path,
        override_path: Path,
        collector: MetricsCollector,
        quality: QualityMonitor,
        state_machine: AutopilotStateMachine,
        remediation: RemediationManager,
        policy_manager: PolicyManager,
        audit: AuditLogger,
        target_state: str,
        enabled: bool,
        policy_shadow_burnin_min_sec: int,
        policy_accept_min_coverage: float,
        policy_accept_max_breaker_storms: int,
        policy_accept_max_data_stale_sec: int,
        safe_hours: Optional[List[str]] = None,
    ) -> None:
        self.state_path = state_path
        self.override_path = override_path
        self.collector = collector
        self.quality = quality
        self.state_machine = state_machine
        self.remediation = remediation
        self.policy_manager = policy_manager
        self.audit = audit
        self.target_state = target_state
        self.enabled = enabled
        self.policy_shadow_burnin_min_sec = max(int(policy_shadow_burnin_min_sec), 0)
        self.policy_accept_min_coverage = float(policy_accept_min_coverage)
        self.policy_accept_max_breaker_storms = int(policy_accept_max_breaker_storms)
        self.policy_accept_max_data_stale_sec = int(policy_accept_max_data_stale_sec)
        self.safe_hours = safe_hours or []
        self.state = self._load_state()

    def tick(self) -> Dict[str, Any]:
        now = time.time()
        override = self._load_override()
        effective_enabled = bool(override.get("enabled", self.enabled))
        if not effective_enabled:
            desired_state = "OFF"
        else:
            desired_state = self.target_state
        override_target = override.get("target_state")
        if override_target:
            desired_state = str(override_target).upper()

        if desired_state in {"LIVE", "LIVE_DEMO"} and self.safe_hours:
            if not _within_safe_hours(self.safe_hours, now):
                desired_state = "SHADOW"

        snapshot = self.collector.collect()
        issues = self.quality.update(snapshot)

        if snapshot.health.status == "UNKNOWN":
            desired_state = "DEGRADED"
            issues.append(QualityIssue("AP_HEALTH_FAIL", "WARN", {"health": snapshot.health.status}))
        if snapshot.health.status == "WARN":
            issues.append(QualityIssue("AP_HEALTH_FAIL", "WARN", {"health": snapshot.health.status}))
        if snapshot.health.status == "FAIL":
            desired_state = "DEGRADED"
            issues.append(QualityIssue("AP_HEALTH_FAIL", "FAIL", {"health": snapshot.health.status}))

        for issue in issues:
            if issue.code in {"AP_BREAKER_STORM", "AP_DATA_STALE", "AP_POLICY_MISMATCH"}:
                desired_state = "DEGRADED"
                break

        if desired_state in {"LIVE", "LIVE_DEMO"}:
            policy_issue = self._policy_acceptance_issue(snapshot)
            if policy_issue:
                issues.append(policy_issue)
                desired_state = "SHADOW"

        new_state = self.state_machine.next_state(self.state, desired_state, now)
        if new_state.state != self.state.state:
            self.audit.log(
                {
                    "action": "STATE_TRANSITION",
                    "from": self.state.state,
                    "to": new_state.state,
                    "reason_codes": [i.code for i in issues] or ["AP_OPERATOR_OVERRIDE" if override else "OK"],
                    "evidence": _snapshot_evidence(snapshot),
                    "correlation_id": _correlation_id(),
                }
            )
            self._save_state(new_state)
            self.state = new_state

        action_result = self._apply_remediation(snapshot, issues)

        return {
            "enabled": effective_enabled,
            "state": self.state.state,
            "target_state": desired_state,
            "issues": [i.__dict__ for i in issues],
            "health": snapshot.health.__dict__,
            "action": action_result.__dict__ if action_result else None,
        }

    def status(self) -> Dict[str, Any]:
        snapshot = self.collector.collect()
        override = self._load_override()
        return {
            "enabled": override.get("enabled", self.enabled),
            "state": self.state.state,
            "target_state": str(override.get("target_state", self.target_state)).upper(),
            "health": snapshot.health.__dict__,
        }

    def _apply_remediation(self, snapshot: MetricsSnapshot, issues: List[Any]):
        evidence = _snapshot_evidence(snapshot)
        for issue in issues:
            if issue.code == "AP_POLICY_MISMATCH":
                return self.remediation.rollback_policy(issue.code, evidence=evidence)
            if issue.code == "AP_BREAKER_STORM":
                return self.remediation.degrade_to_shadow(issue.code, evidence=evidence)
            if issue.code == "AP_DATA_STALE":
                return self.remediation.disable_entries(issue.code, evidence=evidence)
        if snapshot.health.status == "FAIL":
            return self.remediation.restart_quantumedge("AP_HEALTH_FAIL", evidence=evidence)
        if snapshot.health.status == "UNKNOWN":
            return self.remediation.degrade_to_shadow("AP_HEALTH_FAIL", evidence=evidence)
        return None

    def _load_state(self) -> AutopilotState:
        if not self.state_path.exists():
            return AutopilotState(state="OFF", last_transition_ts=0.0, transitions=[])
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            return AutopilotState(
                state=str(payload.get("state", "OFF")),
                last_transition_ts=float(payload.get("last_transition_ts", 0.0) or 0.0),
                transitions=payload.get("transitions", []) or [],
            )
        except json.JSONDecodeError:
            return AutopilotState(state="OFF", last_transition_ts=0.0, transitions=[])

    def _save_state(self, state: AutopilotState) -> None:
        payload = {
            "state": state.state,
            "last_transition_ts": state.last_transition_ts,
            "transitions": state.transitions,
        }
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _load_override(self) -> Dict[str, Any]:
        if not self.override_path.exists():
            return {}
        try:
            return json.loads(self.override_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def _policy_acceptance_issue(self, snapshot: MetricsSnapshot) -> Optional[QualityIssue]:
        record = self.policy_manager.current_record()
        if not record:
            return QualityIssue("AP_POLICY_MISMATCH", "FAIL", {"reason": "policy_missing"})
        applied_at = _parse_iso_ts(record.get("applied_at", ""))
        if applied_at is None:
            return QualityIssue("AP_POLICY_MISMATCH", "FAIL", {"reason": "policy_applied_at_invalid"})
        now = snapshot.ts
        age = now - applied_at
        if self.policy_shadow_burnin_min_sec > 0 and age < self.policy_shadow_burnin_min_sec:
            remaining = int(self.policy_shadow_burnin_min_sec - age)
            return QualityIssue("AP_METRICS_DEGRADED", "WARN", {"policy_burnin_remaining_sec": remaining})

        window = max(
            self.quality.coverage_window_sec,
            self.quality.breaker_storm_window_sec,
            self.quality.data_stale_window_sec,
            60,
        )
        metrics = self.quality.acceptance_metrics(now, window)
        coverage = metrics.get("coverage")
        if self.policy_accept_min_coverage > 0:
            if coverage is None:
                return QualityIssue("AP_METRICS_DEGRADED", "WARN", {"coverage": "missing"})
            if coverage < self.policy_accept_min_coverage:
                return QualityIssue("AP_METRICS_DEGRADED", "WARN", {"coverage": coverage})
        if self.policy_accept_max_breaker_storms > 0:
            breaker_count = metrics.get("breaker_count", 0)
            if breaker_count > self.policy_accept_max_breaker_storms:
                return QualityIssue("AP_BREAKER_STORM", "FAIL", {"count": breaker_count})
        if self.policy_accept_max_data_stale_sec > 0:
            max_tick_age = metrics.get("max_tick_age_ms")
            max_book_age = metrics.get("max_book_age_ms")
            max_stale_ms = max([val for val in [max_tick_age, max_book_age] if val is not None], default=None)
            if max_stale_ms is not None:
                max_stale_sec = max_stale_ms / 1000.0
                if max_stale_sec > self.policy_accept_max_data_stale_sec:
                    return QualityIssue("AP_DATA_STALE", "FAIL", {"max_stale_sec": max_stale_sec})
        return None


def _snapshot_evidence(snapshot: MetricsSnapshot) -> Dict[str, Any]:
    return {
        "mode": snapshot.mode,
        "breaker_active": snapshot.breaker_active,
        "breaker_reason": snapshot.breaker_reason,
        "tick_age_ms": snapshot.tick_age_ms,
        "book_age_ms": snapshot.book_age_ms,
        "last_error": snapshot.last_error,
    }


def _parse_iso_ts(value: str) -> Optional[float]:
    import datetime as _dt

    if not value:
        return None
    try:
        cleaned = value.replace("Z", "+00:00")
        dt = _dt.datetime.fromisoformat(cleaned)
        return dt.timestamp()
    except ValueError:
        return None


def _correlation_id() -> str:
    return str(uuid.uuid4())


def _within_safe_hours(ranges: List[str], now_ts: float) -> bool:
    import datetime as _dt

    now = _dt.datetime.fromtimestamp(now_ts)
    for rng in ranges:
        if "-" not in rng:
            continue
        start_s, end_s = rng.split("-", 1)
        try:
            start = _dt.datetime.strptime(start_s.strip(), "%H:%M").time()
            end = _dt.datetime.strptime(end_s.strip(), "%H:%M").time()
        except ValueError:
            continue
        if start <= end:
            if start <= now.time() <= end:
                return True
        else:
            # overnight window
            if now.time() >= start or now.time() <= end:
                return True
    return False
