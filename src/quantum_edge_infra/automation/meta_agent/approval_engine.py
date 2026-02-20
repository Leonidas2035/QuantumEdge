import json
import os
from datetime import datetime, timezone
from typing import Optional

from .file_manager import ChangeSet, FileChange
from .gate_runner import run_gates
from .logger import configure_logger
from .run_lock import RunLock, describe_existing_lock, resolve_lock_path
from .safety_policy import load_safety_policy
from .shadow_workspace import cleanup_shadow, create_shadow
from .task_contract import TaskSpec, TaskValidationError, load_task_spec
from .write_engine import apply_change_set_with_policy


class ApprovalError(Exception):
    def __init__(self, message: str, status_code: int = 409, exit_code: int = 30):
        super().__init__(message)
        self.status_code = status_code
        self.exit_code = exit_code


def _resolve_base_dir() -> str:
    base = os.path.abspath(os.path.dirname(__file__))
    env_root = os.getenv("QE_ROOT")
    if env_root:
        return env_root
    parent = os.path.abspath(os.path.join(base, os.pardir))
    if os.path.isdir(os.path.join(parent, "config")) and os.path.isdir(
        os.path.join(parent, "ai_scalper_bot")
    ):
        return parent
    return base


def _resolve_runtime_dir() -> str:
    base_abs = os.path.abspath(_resolve_base_dir())
    env_runtime = os.getenv("META_AGENT_RUNTIME_DIR") or os.getenv("QE_RUNTIME_DIR")
    if env_runtime:
        candidate = os.path.abspath(env_runtime)
        try:
            if os.path.commonpath([candidate, base_abs]) == base_abs:
                return candidate
        except ValueError:
            pass
    return os.path.abspath(os.path.join(base_abs, "runtime"))


def _load_report(run_dir: str) -> dict:
    report_path = os.path.join(run_dir, "report.json")
    if not os.path.exists(report_path):
        raise ApprovalError("report.json not found", status_code=404)
    with open(report_path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _load_changeset(run_dir: str) -> ChangeSet:
    path = os.path.join(run_dir, "changeset.json")
    if not os.path.exists(path):
        raise ApprovalError("changeset.json not found", status_code=404)
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle) or {}
    project_root = data.get("project_root") or ""
    changes_raw = data.get("changes") or {}
    change_set = ChangeSet(project_root=project_root, changes={})
    for rel_path, change in changes_raw.items():
        if not isinstance(change, dict):
            continue
        change_set.changes[rel_path] = FileChange(
            path=rel_path,
            old_content=change.get("old_content") or "",
            new_content=change.get("new_content") or "",
        )
    return change_set


def _clone_change_set(change_set: ChangeSet, new_root: str) -> ChangeSet:
    clone = ChangeSet(project_root=os.path.abspath(new_root), changes={})
    for rel, change in change_set.changes.items():
        clone.changes[rel] = FileChange(
            path=rel,
            old_content=change.old_content,
            new_content=change.new_content,
        )
    return clone


def _load_task_spec(run_dir: str) -> TaskSpec:
    task_path = os.path.join(run_dir, "task.yaml")
    if not os.path.exists(task_path):
        raise ApprovalError("task.yaml not found", status_code=404)
    try:
        return load_task_spec(task_path)
    except TaskValidationError as exc:
        raise ApprovalError(
            f"Invalid task spec: {exc}", status_code=400, exit_code=40
        ) from exc


def _write_approval_record(run_dir: str, payload: dict) -> str:
    approval_dir = os.path.join(run_dir, "approval")
    os.makedirs(approval_dir, exist_ok=True)
    path = os.path.join(approval_dir, "approval.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    return path


def _update_report(run_dir: str, update: dict) -> None:
    report_path = os.path.join(run_dir, "report.json")
    with open(report_path, "r", encoding="utf-8") as handle:
        report = json.load(handle)
    report.update(update)
    temp_path = f"{report_path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
    os.replace(temp_path, report_path)


def approve_apply(
    run_id: str,
    runtime_dir: Optional[str] = None,
    method: str = "control_center",
    actor: Optional[str] = None,
) -> dict:
    runtime_root = runtime_dir or _resolve_runtime_dir()
    run_dir = os.path.join(runtime_root, "runs", run_id)
    if not os.path.isdir(run_dir):
        raise ApprovalError("run_id not found", status_code=404)

    log_level = (os.getenv("META_AGENT_LOG_LEVEL") or "INFO").upper()
    logger = configure_logger(
        "meta_agent.approve_apply", runtime_root, log_level, run_id=run_id
    )

    lock_path = resolve_lock_path(_resolve_base_dir())
    lock = RunLock(lock_path)
    if not lock.acquire():
        detail = describe_existing_lock(lock_path)
        message = "Meta-Agent is already running (lock held)."
        if detail:
            message = f"{message} {detail}"
        raise ApprovalError(message, status_code=409, exit_code=50)

    shadow_dir = None
    shadow_strategy = None
    shadow_keep = False
    shadow_project_root = None
    try:
        report = _load_report(run_dir)
        verdict = report.get("verdict")
        exit_code = int(report.get("exit_code") or 0)
        applied = bool(report.get("changes", {}).get("applied"))
        if applied:
            raise ApprovalError("Run already applied", status_code=409)
        if verdict != "warn":
            raise ApprovalError(
                "Approve/apply allowed only for warn verdicts", status_code=409
            )
        if exit_code != 10:
            raise ApprovalError(
                "Approve/apply allowed only for warn (exit_code=10)", status_code=409
            )

        spec = _load_task_spec(run_dir)
        if spec.execution.dry_run:
            raise ApprovalError(
                "Approve/apply not allowed for dry_run tasks", status_code=409
            )

        change_set = _load_changeset(run_dir)
        policy = load_safety_policy()

        shadow_keep = bool(spec.execution.shadow_keep)
        shadow_project_root = change_set.project_root
        shadow_dir = create_shadow(
            change_set.project_root,
            run_dir,
            spec.execution.shadow_strategy,
            logger,
            ignore_globs=spec.constraints.deny_globs,
        )
        shadow_strategy = spec.execution.shadow_strategy
        shadow_change_set = _clone_change_set(change_set, shadow_dir)

        apply_change_set_with_policy(
            shadow_change_set,
            os.path.join(run_dir, "approval", "patches"),
            policy=policy,
            override_verdict="allow",
            force_patch_only=False,
            force_direct=True,
            always_write_patches=False,
        )

        gates_passed = True
        gates_artifacts_dir = os.path.join(run_dir, "approval", "gates")
        if spec.gates.enabled and spec.gates.steps:
            gate_results = run_gates(
                shadow_dir, spec.gates, logger, artifacts_dir=gates_artifacts_dir
            )
            gates_passed = gate_results.passed

        approval_payload = {
            "approved_at": datetime.now(timezone.utc).isoformat(),
            "method": method,
            "actor": actor,
            "gates_passed": gates_passed,
            "applied": False,
        }

        if not gates_passed:
            approval_payload["exit_code"] = 12
            record_path = _write_approval_record(run_dir, approval_payload)
            _update_report(
                run_dir, {"approval": {"record_path": record_path, **approval_payload}}
            )
            return {
                "exit_code": 12,
                "message": "Gates failed; not applied",
                "applied": False,
            }

        outcome = apply_change_set_with_policy(
            change_set,
            os.path.join(run_dir, "approval", "patches"),
            policy=policy,
            override_verdict="allow",
            force_patch_only=False,
            force_direct=True,
            always_write_patches=False,
        )
        approval_payload["applied"] = bool(outcome.applied)
        record_path = _write_approval_record(run_dir, approval_payload)

        report_update = {
            "changes": {
                **(report.get("changes") or {}),
                "applied": bool(outcome.applied),
            },
            "approval": {"record_path": record_path, **approval_payload},
        }
        _update_report(run_dir, report_update)

        return {
            "exit_code": 0 if outcome.applied else 30,
            "message": "Applied" if outcome.applied else "Apply failed",
            "applied": bool(outcome.applied),
        }
    finally:
        if shadow_dir:
            cleanup_shadow(
                shadow_dir,
                keep=shadow_keep,
                strategy=shadow_strategy,
                project_root=shadow_project_root,
                logger=logger,
            )
        lock.release()
