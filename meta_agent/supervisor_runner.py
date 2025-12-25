from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from meta_core import run_task
from projects_config import ProjectRegistry, resolve_project_root
from task_manager import create_task

REPORTS_SUPERVISOR_DIR = Path("reports") / "supervisor"


@dataclass
class BacklogItem:
    project_id: str
    title: str
    instructions: str
    severity: str  # low | normal | high


def _load_reports(report_dir: Path) -> List[Path]:
    if not report_dir.exists():
        return []
    files = []
    for entry in report_dir.iterdir():
        if entry.is_file() and entry.suffix.lower() in {".md", ".json"}:
            files.append(entry)
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def _severity_from_name(name: str) -> str:
    lower = name.lower()
    if "high" in lower or "critical" in lower:
        return "high"
    if "low" in lower:
        return "low"
    return "normal"


def _parse_report(path: Path) -> Dict[str, Any]:
    if path.suffix.lower() == ".json":
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {"title": path.name, "body": "", "severity": _severity_from_name(path.name)}
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return {"title": path.name, "body": "", "severity": _severity_from_name(path.name)}
    lines = [ln.strip() for ln in text.splitlines()]
    title = lines[0].lstrip("# ").strip() if lines else path.name
    body = "\n".join(lines[:40])
    return {"title": title or path.name, "body": body, "severity": _severity_from_name(path.name)}


def _select_project_for_report(report: Dict[str, Any]) -> str:
    title = (report.get("title") or "").lower()
    body = (report.get("body") or "").lower()
    if "supervisor" in title or "supervisor" in body:
        return "supervisor_agent"
    if "meta" in title or "meta" in body:
        return "meta_agent"
    return "ai_scalper_bot"


def build_backlog_from_reports(
    registry: ProjectRegistry,
    max_items: int,
    min_severity: str = "normal",
) -> List[BacklogItem]:
    severity_order = {"low": 0, "normal": 1, "high": 2}
    threshold = severity_order.get(min_severity, 1)
    backlog: List[BacklogItem] = []

    reports = _load_reports(REPORTS_SUPERVISOR_DIR)
    for rep in reports:
        meta = _parse_report(rep)
        sev = meta.get("severity", "normal")
        if severity_order.get(sev, 1) < threshold:
            continue
        project_id = _select_project_for_report(meta)
        if project_id not in registry.projects:
            project_id = registry.default_project_id

        instructions = (
            "# Supervisor Report Context\n"
            f"Source report: {rep.name}\n"
            f"Severity: {sev}\n\n"
            "Use the context below to propose fixes or follow-ups:\n\n"
            f"{meta.get('body','')}\n"
        )
        backlog.append(
            BacklogItem(
                project_id=project_id,
                title=meta.get("title", rep.stem),
                instructions=instructions,
                severity=sev,
            )
        )
        if len(backlog) >= max_items:
            break
    return backlog


def run_supervisor_maintenance_once(registry: ProjectRegistry, schedule_cfg: Dict[str, Any]) -> Dict[str, Any]:
    backlog_cfg = schedule_cfg.get("backlog", {}) or {}
    max_items = int(backlog_cfg.get("max_items_per_run", 5))
    min_sev = str(backlog_cfg.get("min_severity", "normal")).lower()

    backlog = build_backlog_from_reports(registry, max_items=max_items, min_severity=min_sev)
    if not backlog:
        return {"status": "no_backlog", "tasks": []}

    tasks_summary: List[Dict[str, Any]] = []
    for item in backlog:
        try:
            project_info = resolve_project_root(item.project_id, registry)
        except KeyError:
            project_info = resolve_project_root(None, registry)

        body = (
            f"# Supervisor Follow-up\n"
            f"Project: {item.project_id}\n"
            f"Severity: {item.severity}\n\n"
            f"{item.instructions}\n"
        )
        task = create_task(
            project=item.project_id,
            task_type="supervisor_followup",
            title=item.title,
            body_markdown=body,
            priority="high" if item.severity == "high" else "normal",
            source="offmarket_supervisor",
        )
        result = run_task(task.task_id)
        result["project"] = item.project_id
        tasks_summary.append(result)

    return {"status": "ok", "tasks": tasks_summary}
