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


def _task_from_dict(raw: dict) -> TaskSpec:
    if not isinstance(raw, dict):
        raise TaskValidationError("TaskSpec must be a mapping")
    task_id = raw.get("task_id") or _generate_task_id(str(raw.get("project_id") or "task"))
    created_at = raw.get("created_at") or _now_iso()
    if isinstance(created_at, datetime):
        created_at = created_at.astimezone(timezone.utc).isoformat()
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
