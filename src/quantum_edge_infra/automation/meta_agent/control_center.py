import json
import os
import uuid
from datetime import datetime, timezone
from typing import List, Optional

import yaml

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - fallback
    ZoneInfo = None

from approval_engine import approve_apply
from projects_registry import ProjectEntry, load_projects_registry
from schedule_contract import ScheduleValidationError, load_schedule_file
from offmarket_scheduler import evaluate_windows
from task_contract import TaskConstraints, TaskContext, TaskExecution, TaskGates, TaskLLM, TaskSpec, GateStep


def _resolve_base_dir() -> str:
    base = os.path.abspath(os.path.dirname(__file__))
    env_root = os.getenv("QE_ROOT")
    if env_root:
        return env_root
    parent = os.path.abspath(os.path.join(base, os.pardir))
    if os.path.isdir(os.path.join(parent, "config")) and os.path.isdir(os.path.join(parent, "ai_scalper_bot")):
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


def _tzinfo(name: str):
    if ZoneInfo is None:
        raise ValueError("ZoneInfo unavailable; timezone support missing.")
    return ZoneInfo(name)


def _control_state_path(runtime_dir: str) -> str:
    return os.path.join(runtime_dir, "control_center", "state.json")


def _save_state_atomic(path: str, state: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp_path = f"{path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2)
    os.replace(temp_path, path)


def load_control_state(runtime_dir: Optional[str] = None) -> dict:
    runtime_root = runtime_dir or _resolve_runtime_dir()
    path = _control_state_path(runtime_root)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle) or {}
        except Exception:
            return {}
    return {}


def ensure_active_project(projects: List[ProjectEntry], runtime_dir: Optional[str] = None) -> str:
    runtime_root = runtime_dir or _resolve_runtime_dir()
    state = load_control_state(runtime_root)
    active = state.get("active_project")
    project_ids = [p.project_id for p in projects]
    if active in project_ids:
        return active
    active = project_ids[0] if project_ids else ""
    state["active_project"] = active
    _save_state_atomic(_control_state_path(runtime_root), state)
    return active


def set_active_project(project_id: str, runtime_dir: Optional[str] = None) -> None:
    runtime_root = runtime_dir or _resolve_runtime_dir()
    state = load_control_state(runtime_root)
    state["active_project"] = project_id
    _save_state_atomic(_control_state_path(runtime_root), state)


def _resolve_project(projects: List[ProjectEntry], project_id: str) -> Optional[ProjectEntry]:
    for entry in projects:
        if entry.project_id == project_id:
            return entry
    return None


def _generate_task_id(project_id: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    short = uuid.uuid4().hex[:6]
    safe_project = "".join(ch if ch.isalnum() else "_" for ch in project_id).strip("_") or "task"
    return f"T{stamp}_{safe_project}_{short}"


def create_task_inbox(payload: dict, runtime_dir: Optional[str] = None) -> dict:
    runtime_root = runtime_dir or _resolve_runtime_dir()
    projects = load_projects_registry()
    active_project = ensure_active_project(projects, runtime_root)
    project_id = str(payload.get("project_id") or active_project)
    project = _resolve_project(projects, project_id)

    task_id = _generate_task_id(project_id)
    created_at = datetime.now(timezone.utc).isoformat()

    constraints = payload.get("constraints") or {}
    context = payload.get("context") or {}
    execution = payload.get("execution") or {}
    gates_payload = payload.get("gates") or {}

    deny_globs = constraints.get("deny_globs") or (project.deny_globs if project else [])
    include_globs = context.get("include_globs") or (project.default_include_globs if project else [])

    gates_steps = gates_payload.get("steps") or []
    if payload.get("gates_preset"):
        gates_steps = [{"name": "smoke", "cmd": ["python", "-c", "import sys; sys.exit(0)"]}]

    spec = TaskSpec(
        task_id=task_id,
        created_at=created_at,
        project_id=project_id,
        project_root=payload.get("project_root"),
        objective=str(payload.get("objective") or ""),
        instructions=str(payload.get("instructions") or ""),
        constraints=TaskConstraints(
            patch_only=bool(constraints.get("patch_only", False)),
            max_files=int(constraints.get("max_files", 20)),
            max_file_bytes=int(constraints.get("max_file_bytes", 262_144)),
            deny_globs=list(deny_globs),
        ),
        context=TaskContext(
            include_globs=list(include_globs),
            focus_files=list(context.get("focus_files") or []),
        ),
        llm=TaskLLM(
            model=(payload.get("llm") or {}).get("model"),
            temperature=(payload.get("llm") or {}).get("temperature"),
            max_context_chars=(payload.get("llm") or {}).get("max_context_chars"),
        ),
        execution=TaskExecution(
            dry_run=bool(execution.get("dry_run", False)),
            shadow=bool(execution.get("shadow", True)),
            shadow_strategy=str(execution.get("shadow_strategy", "copy")),
            shadow_keep=bool(execution.get("shadow_keep", False)),
        ),
        gates=TaskGates(
            enabled=bool(gates_payload.get("enabled", bool(gates_steps))),
            steps=[
                GateStep(
                    name=step.get("name", ""),
                    cmd=list(step.get("cmd") or []),
                    cwd=step.get("cwd"),
                    timeout_seconds=int(step.get("timeout_seconds", 300)),
                    env=step.get("env"),
                    continue_on_fail=bool(step.get("continue_on_fail", False)),
                )
                for step in gates_steps
            ],
        ),
        mode="task",
        metadata={"source": "control_center"},
    )
    spec.validate()

    inbox_dir = os.path.join(runtime_root, "inbox")
    os.makedirs(inbox_dir, exist_ok=True)
    filename = f"{task_id}.yaml"
    path = os.path.join(inbox_dir, filename)
    temp_path = f"{path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        yaml.safe_dump(spec.to_dict(), handle, allow_unicode=True, sort_keys=False)
    os.replace(temp_path, path)

    return {
        "task_id": task_id,
        "path": path,
        "filename": filename,
        "project_id": project_id,
    }


def list_inbox(runtime_dir: Optional[str] = None) -> List[dict]:
    runtime_root = runtime_dir or _resolve_runtime_dir()
    inbox_dir = os.path.join(runtime_root, "inbox")
    if not os.path.isdir(inbox_dir):
        return []
    items = []
    for entry in sorted(os.listdir(inbox_dir)):
        if entry.lower() in {"stop", "pause"}:
            continue
        if entry.lower().endswith((".yaml", ".yml", ".md", ".markdown")):
            path = os.path.join(inbox_dir, entry)
            items.append(
                {
                    "name": entry,
                    "path": path,
                    "mtime": os.path.getmtime(path),
                }
            )
    return items


def list_runs(runtime_dir: Optional[str] = None, limit: int = 50, verdict: str = "any") -> List[dict]:
    runtime_root = runtime_dir or _resolve_runtime_dir()
    runs_dir = os.path.join(runtime_root, "runs")
    if not os.path.isdir(runs_dir):
        return []
    reports = []
    for entry in os.listdir(runs_dir):
        report_path = os.path.join(runs_dir, entry, "report.json")
        if not os.path.exists(report_path):
            continue
        try:
            with open(report_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            reports.append(data)
        except Exception:
            continue

    def _match(rep: dict) -> bool:
        if verdict in {"any", "", None}:
            return True
        exit_code = int(rep.get("exit_code") or 0)
        if verdict == "gate_failed":
            return exit_code == 12
        if verdict == "dry_run":
            return exit_code == 11
        return rep.get("verdict") == verdict

    reports = [r for r in reports if _match(r)]
    reports.sort(key=lambda r: r.get("finished_at", ""), reverse=True)
    output = []
    for rep in reports[:limit]:
        output.append(
            {
                "run_id": rep.get("run_id"),
                "task_id": rep.get("task_id"),
                "verdict": rep.get("verdict"),
                "exit_code": rep.get("exit_code"),
                "applied": (rep.get("changes") or {}).get("applied"),
                "finished_at": rep.get("finished_at"),
                "report_path": (rep.get("artifacts") or {}).get("report_path"),
            }
        )
    return output


def get_run_detail(run_id: str, runtime_dir: Optional[str] = None) -> dict:
    runtime_root = runtime_dir or _resolve_runtime_dir()
    run_dir = os.path.join(runtime_root, "runs", run_id)
    report_path = os.path.join(run_dir, "report.json")
    if not os.path.exists(report_path):
        raise FileNotFoundError("report.json not found")
    with open(report_path, "r", encoding="utf-8") as handle:
        report = json.load(handle)

    patches_dir = os.path.join(run_dir, "patches")
    gates_dir = os.path.join(run_dir, "gates")
    approval_gates_dir = os.path.join(run_dir, "approval", "gates")

    patch_files = []
    if os.path.isdir(patches_dir):
        for root, _, files in os.walk(patches_dir):
            for name in files:
                rel = os.path.relpath(os.path.join(root, name), patches_dir)
                patch_files.append(rel.replace("\\", "/"))

    gate_files = []
    if os.path.isdir(gates_dir):
        for name in sorted(os.listdir(gates_dir)):
            gate_files.append(name)

    approval_gate_files = []
    if os.path.isdir(approval_gates_dir):
        for name in sorted(os.listdir(approval_gates_dir)):
            approval_gate_files.append(f"approval/{name}")

    return {
        "run_id": run_id,
        "report": report,
        "patch_files": patch_files,
        "gate_files": gate_files,
        "approval_gate_files": approval_gate_files,
    }


def list_schedules_with_state(schedules_dir: str, runtime_dir: Optional[str] = None) -> List[dict]:
    runtime_root = runtime_dir or _resolve_runtime_dir()
    state_path = os.path.join(runtime_root, "scheduler", "state.json")
    state = {"schedules": {}}
    if os.path.exists(state_path):
        try:
            with open(state_path, "r", encoding="utf-8") as handle:
                state = json.load(handle) or state
        except Exception:
            state = {"schedules": {}}

    entries = []
    if not os.path.isdir(schedules_dir):
        return entries

    for entry in sorted(os.listdir(schedules_dir)):
        if not entry.lower().endswith((".yaml", ".yml")):
            continue
        path = os.path.join(schedules_dir, entry)
        try:
            specs = load_schedule_file(path)
        except (ScheduleValidationError, FileNotFoundError):
            continue
        for spec in specs:
            status = state.get("schedules", {}).get(spec.schedule_id, {})
            entries.append(
                {
                    "schedule_id": spec.schedule_id,
                    "enabled": spec.enabled,
                    "timezone": spec.timezone,
                    "source": path,
                    "in_window": evaluate_windows(datetime.now(timezone.utc).astimezone(_tzinfo(spec.timezone)), spec.windows),
                    "next_eligible_at": status.get("next_eligible_at"),
                    "last_exit_code": status.get("last_exit_code"),
                    "attempts": status.get("attempts", 0),
                }
            )
    return entries


def toggle_schedule(schedule_id: str, enabled: bool, schedules_dir: str) -> None:
    if not os.path.isdir(schedules_dir):
        raise FileNotFoundError("Schedules directory not found")
    for entry in sorted(os.listdir(schedules_dir)):
        if not entry.lower().endswith((".yaml", ".yml")):
            continue
        path = os.path.join(schedules_dir, entry)
        with open(path, "r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
        updated = False
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict) and item.get("schedule_id") == schedule_id:
                    item["enabled"] = bool(enabled)
                    updated = True
        elif isinstance(raw, dict):
            if "schedules" in raw and isinstance(raw.get("schedules"), list):
                for item in raw.get("schedules") or []:
                    if isinstance(item, dict) and item.get("schedule_id") == schedule_id:
                        item["enabled"] = bool(enabled)
                        updated = True
            elif raw.get("schedule_id") == schedule_id:
                raw["enabled"] = bool(enabled)
                updated = True
        if updated:
            temp_path = f"{path}.tmp"
            with open(temp_path, "w", encoding="utf-8") as handle:
                yaml.safe_dump(raw, handle, allow_unicode=True, sort_keys=False)
            os.replace(temp_path, path)
            return
    raise FileNotFoundError("Schedule id not found")


def approve_apply_run(run_id: str, runtime_dir: Optional[str] = None, actor: Optional[str] = None) -> dict:
    runtime_root = runtime_dir or _resolve_runtime_dir()
    return approve_apply(run_id, runtime_root, method="control_center", actor=actor)
