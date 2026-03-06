"""CLI helpers for autopilot and policy rollout."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Dict

from quantum_edge_core.supervisor.supervisor.autopilot.audit import AuditLogger
from quantum_edge_core.supervisor.supervisor.autopilot.collector import MetricsCollector
from quantum_edge_core.supervisor.supervisor.autopilot.policy_manager import (
    PolicyManager,
)
from quantum_edge_core.supervisor.supervisor.autopilot.quality import QualityMonitor
from quantum_edge_core.supervisor.supervisor.autopilot.remediation import (
    RemediationManager,
)
from quantum_edge_core.supervisor.supervisor.autopilot.state_machine import (
    AutopilotController,
    AutopilotStateMachine,
)
from quantum_edge_core.supervisor.supervisor.config import AutopilotConfig, PathsConfig


def build_controller(
    app, cfg: AutopilotConfig, paths: PathsConfig
) -> AutopilotController:
    metrics_path = _resolve_path(paths.qe_root, cfg.metrics_path)
    collector = MetricsCollector(cfg.metrics_url, metrics_path)
    quality = QualityMonitor(
        breaker_storm_threshold=cfg.quality_breaker_storm_threshold,
        breaker_storm_window_sec=cfg.quality_breaker_storm_window_sec,
        coverage_min=cfg.quality_coverage_min,
        coverage_window_sec=cfg.quality_coverage_window_sec,
        latency_p95_ms=cfg.quality_latency_p95_ms,
        data_stale_ms=cfg.quality_data_stale_ms,
        data_stale_window_sec=cfg.quality_data_stale_window_sec,
        policy_mismatch_reject_ratio=cfg.quality_policy_mismatch_reject_ratio,
    )
    state_machine = AutopilotStateMachine(
        cfg.allowed_states, cfg.min_dwell_sec, cfg.max_transitions_per_hour
    )
    audit_path = paths.runtime_dir / "audit" / "autopilot_actions.jsonl"
    audit = AuditLogger(audit_path)
    policy_dir = _resolve_path(paths.qe_root, cfg.policy_artifacts_dir)
    runtime_policy_dir = _resolve_path(paths.qe_root, cfg.policy_runtime_dir)
    history_dir = paths.runtime_dir / "policy_rollouts" / cfg.policy_symbol
    policy_manager = PolicyManager(
        policy_dir, runtime_policy_dir, history_dir, cfg.policy_history_keep
    )
    kill_switch_path = paths.quantumedge_root / "state" / "kill_switch.json"
    remediation = RemediationManager(
        app=app,
        policy_manager=policy_manager,
        audit=audit,
        kill_switch_path=kill_switch_path,
        restart_max_per_hour=cfg.remediation_restart_max_per_hour,
        restart_cooldown_sec=cfg.remediation_restart_cooldown_sec,
        max_actions_per_hour=cfg.max_actions_per_hour,
        disable_entries_on_degrade=cfg.remediation_disable_entries_on_degrade,
    )
    state_path = paths.runtime_dir / "autopilot" / "state.json"
    override_path = paths.runtime_dir / "autopilot" / "override.json"
    return AutopilotController(
        state_path=state_path,
        override_path=override_path,
        collector=collector,
        quality=quality,
        state_machine=state_machine,
        remediation=remediation,
        policy_manager=policy_manager,
        audit=audit,
        target_state=cfg.target_state,
        enabled=cfg.enabled,
        policy_shadow_burnin_min_sec=cfg.policy_shadow_burnin_min_sec,
        policy_accept_min_coverage=cfg.policy_accept_min_coverage,
        policy_accept_max_breaker_storms=cfg.policy_accept_max_breaker_storms,
        policy_accept_max_data_stale_sec=cfg.policy_accept_max_data_stale_sec,
        safe_hours=cfg.safe_hours,
    )


def autopilot_status(controller: AutopilotController) -> Dict[str, Any]:
    return controller.status()


def autopilot_enable(
    override_path: Path, enabled: bool, audit: AuditLogger | None = None
) -> Dict[str, Any]:
    override_path.parent.mkdir(parents=True, exist_ok=True)
    payload: Dict[str, Any] = {}
    if override_path.exists():
        try:
            payload = json.loads(override_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}
    payload["enabled"] = enabled
    override_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if audit:
        audit.log(
            {
                "action": "AUTOPILOT_OVERRIDE",
                "applied": True,
                "enabled": enabled,
                "correlation_id": str(uuid.uuid4()),
            }
        )
    return payload


def autopilot_set_target_state(
    override_path: Path, target_state: str, audit: AuditLogger | None = None
) -> Dict[str, Any]:
    override_path.parent.mkdir(parents=True, exist_ok=True)
    payload: Dict[str, Any] = {}
    if override_path.exists():
        try:
            payload = json.loads(override_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}
    payload["target_state"] = str(target_state).upper()
    override_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if audit:
        audit.log(
            {
                "action": "AUTOPILOT_TARGET_STATE",
                "applied": True,
                "target_state": payload["target_state"],
                "correlation_id": str(uuid.uuid4()),
            }
        )
    return payload


def policy_list(manager: PolicyManager) -> Dict[str, Any]:
    history = manager.list_history()
    return {
        "current": str(manager.current_policy()) if manager.current_policy() else None,
        "history": [
            {
                "policy_path": str(r.policy_path),
                "applied_at": r.applied_at,
                "status": r.status,
                "reason": r.reason,
                "policy_hash": r.policy_hash,
            }
            for r in history
        ],
    }


def policy_rollout(
    manager: PolicyManager, path: Path, reason: str, audit: AuditLogger | None = None
) -> Dict[str, Any]:
    dest = manager.rollout(path, reason)
    if audit:
        audit.log(
            {
                "action": "POLICY_ROLLOUT",
                "applied": True,
                "reason": reason,
                "policy_path": str(path),
                "correlation_id": str(uuid.uuid4()),
            }
        )
    return {"status": "applied", "runtime_path": str(dest)}


def policy_rollback(
    manager: PolicyManager, reason: str, audit: AuditLogger | None = None
) -> Dict[str, Any]:
    dest = manager.rollback(reason)
    if audit:
        audit.log(
            {
                "action": "POLICY_ROLLBACK",
                "applied": bool(dest),
                "reason": reason,
                "correlation_id": str(uuid.uuid4()),
            }
        )
    return {
        "status": "rollback" if dest else "no_history",
        "runtime_path": str(dest) if dest else None,
    }


def _resolve_path(base: Path, value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = base / path
    return path.resolve()
