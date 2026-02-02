import os
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
REPORTS_DIR = os.path.join(BASE_DIR, "reports")


@dataclass
class Report:
    task_id: str
    project: str
    task_type: str
    title: str
    priority: str = "normal"

    status: str = "ok"          # "ok" | "error" | "partial" | "blocked"
    error_message: Optional[str] = None

    summary: str = ""
    changed_files: List[str] = field(default_factory=list)
    created_files: List[str] = field(default_factory=list)
    deleted_files: List[str] = field(default_factory=list)

    risks: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    safety_status: str = "allow"            # "allow" | "warn" | "block"
    blocked_files: List[str] = field(default_factory=list)
    warning_files: List[str] = field(default_factory=list)
    patch_files: List[str] = field(default_factory=list)

    meta: Dict[str, Any] = field(default_factory=dict)


def _ensure_reports_dir() -> None:
    os.makedirs(REPORTS_DIR, exist_ok=True)


def report_path_json(task_id: str) -> str:
    _ensure_reports_dir()
    filename = f"{task_id}_report.json"
    return os.path.join(REPORTS_DIR, filename)


def report_path_md(task_id: str) -> str:
    _ensure_reports_dir()
    filename = f"{task_id}_report.md"
    return os.path.join(REPORTS_DIR, filename)


def write_json_report(report: Report) -> str:
    path = report_path_json(report.task_id)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(json_dumps(report))
    return path


def write_md_report(report: Report) -> str:
    path = report_path_md(report.task_id)
    lines: List[str] = [
        f"# Task Report: {report.task_id}",
        f"- Project: {report.project}",
        f"- Type: {report.task_type}",
        f"- Title: {report.title}",
        f"- Priority: {report.priority}",
        f"- Status: {report.status}",
        f"- Safety: {report.safety_status}",
    ]
    if report.error_message:
        lines.append(f"- Error: {report.error_message}")
    if report.meta:
        started = report.meta.get("started_at")
        finished = report.meta.get("finished_at")
        model = report.meta.get("model")
        source = report.meta.get("source")
        task_path = report.meta.get("task_path")
        if started:
            lines.append(f"- Started: {started}")
        if finished:
            lines.append(f"- Finished: {finished}")
        if model:
            lines.append(f"- Model: {model}")
        if source:
            lines.append(f"- Source: {source}")
        if task_path:
            lines.append(f"- Task Path: {task_path}")

    lines += [
        "",
        "## Summary",
        report.summary or "No summary.",
        "",
        "## Changed Files",
        *(report.changed_files or ["- none"]),
        "",
        "## Created Files",
        *(report.created_files or ["- none"]),
        "",
        "## Deleted Files",
        *(report.deleted_files or ["- none"]),
        "",
        "## Patch Files",
        *(report.patch_files or ["- none"]),
        "",
    ]
    if report.blocked_files:
        lines.append("## Blocked Files")
        lines.extend(f"- {f}" for f in report.blocked_files)
        lines.append("")
    if report.warning_files:
        lines.append("## Warning Files")
        lines.extend(f"- {f}" for f in report.warning_files)
        lines.append("")
    if report.risks:
        lines.append("## Risks")
        lines.extend(f"- {risk}" for risk in report.risks)
        lines.append("")
    if report.notes:
        lines.append("## Notes")
        lines.extend(f"- {note}" for note in report.notes)
        lines.append("")
    if report.meta.get("quality_checks"):
        qc = report.meta["quality_checks"]
        lines.append("## Quality Checks")
        lines.append(f"- Tests run: {qc.get('tests_run')}, status: {qc.get('tests_status')}")
        compile_errors = qc.get("compile_errors") or {}
        if compile_errors:
            lines.append("- Compile errors:")
            for path, msg in compile_errors.items():
                lines.append(f"  - {path}: {msg}")
        lines.append("")

    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    return path


def json_dumps(report: Report) -> str:
    """
    Dump report dataclass to a pretty JSON string.
    """
    import json

    return json.dumps(asdict(report), ensure_ascii=True, indent=2)
