import os
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import yaml


class TaskValidationError(Exception):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


DEFAULT_DENY_GLOBS = [
    ".env",
    "**/.env",
    "**/*.env",
    "**/secrets*",
    "**/*.key",
    "**/*.pem",
    "**/*.pfx",
    "**/*.p12",
    "**/*.enc",
    "**/.venv/**",
    "**/venv/**",
    "**/__pycache__/**",
    "**/.git/**",
    "**/runtime/**",
    "**/logs/**",
    "**/data/**",
    "**/artifacts/**",
    "**/output/**",
    "**/patches/**",
]

SENSITIVE_ENV_KEYWORDS = ("KEY", "SECRET", "TOKEN", "PASSWORD")


def _generate_task_id(project_id: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    short = uuid.uuid4().hex[:6]
    safe_project = "".join(ch if ch.isalnum() else "_" for ch in project_id).strip("_") or "task"
    return f"T{stamp}_{safe_project}_{short}"


@dataclass
class TaskConstraints:
    patch_only: bool = False
    max_files: int = 20
    max_file_bytes: int = 262_144
    deny_globs: List[str] = field(default_factory=list)


@dataclass
class TaskContext:
    include_globs: List[str] = field(default_factory=list)
    focus_files: List[str] = field(default_factory=list)


@dataclass
class TaskLLM:
    model: Optional[str] = None
    temperature: Optional[float] = None
    max_context_chars: Optional[int] = None


@dataclass
class TaskExecution:
    dry_run: bool = False
    shadow: bool = False
    shadow_strategy: str = "copy"
    shadow_keep: bool = False


@dataclass
class GateStep:
    name: str
    cmd: List[str]
    cwd: Optional[str] = None
    timeout_seconds: int = 300
    env: Optional[Dict[str, str]] = None
    continue_on_fail: bool = False


@dataclass
class TaskGates:
    enabled: bool = False
    steps: List[GateStep] = field(default_factory=list)


@dataclass
class TaskSpec:
    task_id: str
    created_at: str
    project_id: str
    project_root: Optional[str]
    objective: str
    instructions: str
    constraints: TaskConstraints = field(default_factory=TaskConstraints)
    context: TaskContext = field(default_factory=TaskContext)
    llm: TaskLLM = field(default_factory=TaskLLM)
    execution: TaskExecution = field(default_factory=TaskExecution)
    gates: TaskGates = field(default_factory=TaskGates)
    mode: str = "task"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        errors = []
        if not self.project_id:
            errors.append("project_id is required")
        if not self.objective:
            errors.append("objective is required")
        if not self.instructions:
            errors.append("instructions is required")
        if self.mode != "task":
            errors.append("mode must be 'task'")
        if self.constraints.max_files <= 0:
            errors.append("constraints.max_files must be > 0")
        if self.constraints.max_file_bytes <= 0:
            errors.append("constraints.max_file_bytes must be > 0")
        if self.gates.enabled and not self.gates.steps:
            errors.append("gates.enabled requires at least one gate step")
        if self.execution.shadow_strategy not in {"copy", "git_worktree"}:
            errors.append("execution.shadow_strategy must be copy or git_worktree")
        if errors:
            raise TaskValidationError("; ".join(errors))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PatchInfo:
    path: str
    patch_file: str
    sha_before: Optional[str] = None
    sha_after: Optional[str] = None


@dataclass
class ReportChanges:
    patches: List[PatchInfo] = field(default_factory=list)
    applied: bool = False
    files_changed: int = 0


@dataclass
class ReportSafety:
    policy_version: Optional[str] = None
    checks: List[str] = field(default_factory=list)


@dataclass
class ReportArtifacts:
    report_path: str
    patches_dir: str
    logs_path: Optional[str]
    context_manifest_path: str
    task_path: str


@dataclass
class ReportCLI:
    command: str
    args_sanitized: List[str]


@dataclass
class ReportDurations:
    total_ms: int
    scan_ms: int
    llm_ms: int
    safety_ms: int
    apply_ms: int


@dataclass
class ReportGateStep:
    name: str
    exit_code: Optional[int]
    duration_ms: int
    stdout_path: Optional[str]
    stderr_path: Optional[str]
    timed_out: bool
    error: Optional[str] = None


@dataclass
class ReportGates:
    enabled: bool
    passed: bool
    steps: List[ReportGateStep]
    artifacts_dir: Optional[str]
    started_at: str
    finished_at: str


@dataclass
class ReportShadow:
    used: bool
    strategy: Optional[str]
    kept: bool
    shadow_dir_rel: Optional[str]


@dataclass
class Report:
    run_id: str
    task_id: str
    started_at: str
    finished_at: str
    verdict: str
    exit_code: int
    summary: str
    changes: ReportChanges
    safety: ReportSafety
    artifacts: ReportArtifacts
    errors: List[str] = field(default_factory=list)
    cli: Optional[ReportCLI] = None
    runtime_dir: Optional[str] = None
    durations: Optional[ReportDurations] = None
    gates: Optional[ReportGates] = None
    shadow: Optional[ReportShadow] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _parse_frontmatter(text: str) -> tuple[Optional[dict], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, text
    end = None
    for idx, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end = idx
            break
    if end is None:
        return None, text
    meta_text = "\n".join(lines[1:end])
    body = "\n".join(lines[end + 1:]).lstrip()
    data = yaml.safe_load(meta_text) or {}
    return data, body


def _normalize_constraints(raw: dict) -> TaskConstraints:
    constraints = raw or {}
    deny = list(constraints.get("deny_globs", []) or [])
    if not deny:
        deny = DEFAULT_DENY_GLOBS.copy()
    return TaskConstraints(
        patch_only=bool(constraints.get("patch_only", False)),
        max_files=int(constraints.get("max_files", 20)),
        max_file_bytes=int(constraints.get("max_file_bytes", 262_144)),
        deny_globs=deny,
    )


def _normalize_context(raw: dict) -> TaskContext:
    context = raw or {}
    return TaskContext(
        include_globs=list(context.get("include_globs", []) or []),
        focus_files=list(context.get("focus_files", []) or []),
    )


def _normalize_llm(raw: dict) -> TaskLLM:
    llm = raw or {}
    temperature = llm.get("temperature")
    max_context_chars = llm.get("max_context_chars")
    return TaskLLM(
        model=llm.get("model"),
        temperature=float(temperature) if temperature is not None else None,
        max_context_chars=int(max_context_chars) if max_context_chars is not None else None,
    )


def _normalize_execution(raw: dict, has_gates: bool, dry_run: bool) -> TaskExecution:
    execution = raw or {}
    shadow = execution.get("shadow")
    if shadow is None:
        shadow = bool(has_gates or dry_run)
    return TaskExecution(
        dry_run=bool(execution.get("dry_run", dry_run)),
        shadow=bool(shadow),
        shadow_strategy=str(execution.get("shadow_strategy", "copy")),
        shadow_keep=bool(execution.get("shadow_keep", False)),
    )


def _validate_gate_env(env: Optional[Dict[str, str]]) -> None:
    if not env:
        return
    for key in env.keys():
        upper = key.upper()
        if any(word in upper for word in SENSITIVE_ENV_KEYWORDS):
            raise TaskValidationError(f"Gate env key not allowed: {key}")


def _normalize_gate_steps(raw_steps: list) -> List[GateStep]:
    steps: List[GateStep] = []
    for entry in raw_steps:
        if not isinstance(entry, dict):
            raise TaskValidationError("Gate step must be a mapping")
        name = str(entry.get("name") or "")
        cmd = entry.get("cmd")
        if not name:
            raise TaskValidationError("Gate step missing name")
        if not isinstance(cmd, list) or not cmd:
            raise TaskValidationError("Gate step cmd must be a non-empty list")
        if not all(isinstance(c, str) for c in cmd):
            raise TaskValidationError("Gate step cmd must be list of strings")
        env = entry.get("env")
        if env is not None and not isinstance(env, dict):
            raise TaskValidationError("Gate step env must be a mapping")
        _validate_gate_env(env)
        steps.append(
            GateStep(
                name=name,
                cmd=cmd,
                cwd=entry.get("cwd"),
                timeout_seconds=int(entry.get("timeout_seconds", 300)),
                env=env,
                continue_on_fail=bool(entry.get("continue_on_fail", False)),
            )
        )
    return steps


def _normalize_gates(raw: dict) -> TaskGates:
    gates = raw or {}
    steps_raw = list(gates.get("steps", []) or [])
    steps = _normalize_gate_steps(steps_raw)
    enabled = gates.get("enabled")
    if enabled is None:
        enabled = bool(steps)
    return TaskGates(enabled=bool(enabled), steps=steps)


def _task_from_dict(raw: dict) -> TaskSpec:
    if not isinstance(raw, dict):
        raise TaskValidationError("TaskSpec must be a mapping")
    task_id = raw.get("task_id") or _generate_task_id(str(raw.get("project_id") or "task"))
    created_at = raw.get("created_at") or _now_iso()
    if isinstance(created_at, datetime):
        created_at = created_at.astimezone(timezone.utc).isoformat()
    dry_run_hint = bool((raw.get("execution") or {}).get("dry_run", False))
    gates_block = raw.get("gates") or {}
    gates = _normalize_gates(gates_block)
    spec = TaskSpec(
        task_id=str(task_id),
        created_at=str(created_at),
        project_id=str(raw.get("project_id") or ""),
        project_root=raw.get("project_root"),
        objective=str(raw.get("objective") or ""),
        instructions=str(raw.get("instructions") or ""),
        constraints=_normalize_constraints(raw.get("constraints") or {}),
        context=_normalize_context(raw.get("context") or {}),
        llm=_normalize_llm(raw.get("llm") or {}),
        execution=_normalize_execution(raw.get("execution") or {}, gates.enabled, dry_run_hint),
        gates=gates,
        mode=str(raw.get("mode") or "task"),
        metadata=dict(raw.get("metadata") or {}),
    )
    spec.validate()
    return spec


def load_task_spec(path: str) -> TaskSpec:
    if not os.path.exists(path):
        raise TaskValidationError(f"Task file not found: {path}")
    ext = os.path.splitext(path)[1].lower()

    if ext in {".yaml", ".yml"}:
        with open(path, "r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
        return _task_from_dict(raw)

    if ext in {".md", ".markdown"}:
        with open(path, "r", encoding="utf-8") as handle:
            content = handle.read()
        meta, body = _parse_frontmatter(content)
        if meta is not None:
            if not meta.get("instructions") and body:
                meta = dict(meta)
                meta["instructions"] = body
            return _task_from_dict(meta)

    try:
        from task_schema import parse_task_file

        legacy = parse_task_file(path)
        raw = {
            "task_id": legacy.task_id,
            "created_at": legacy.created_at or _now_iso(),
            "project_id": legacy.project,
            "objective": legacy.title,
            "instructions": legacy.body_markdown,
            "metadata": {
                "task_type": legacy.task_type,
                "priority": legacy.priority,
                "source": legacy.source,
            },
        }
        return _task_from_dict(raw)
    except Exception as exc:
        raise TaskValidationError(f"Unsupported task format: {exc}") from exc
