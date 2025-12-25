import json
import os
import subprocess
from datetime import datetime
from typing import Dict, List

from codex_client import CodexClient
from file_manager import (
    apply_change_set_direct,
    build_change_set_from_response,
    write_change_set_as_patches,
)
from project_scanner import ProjectScanner
from prompt_builder import PromptBuilder
from report_schema import Report, write_json_report, write_md_report
from safety_policy import evaluate_change_set, load_safety_policy
from task_manager import load_task
from task_schema import TaskParseError

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
TASKS_DIR = os.path.join(BASE_DIR, "tasks")
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
PATCHES_DIR = os.path.join(BASE_DIR, "patches")


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _load_config(path: str = CONFIG_PATH) -> Dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (json.JSONDecodeError, OSError):
        return {}


def _build_summary(status: str, changed_files: List[str], created_files: List[str], error_message: str | None) -> str:
    if status != "ok":
        return f"Task failed: {error_message}" if error_message else "Task failed."
    touched = list(dict.fromkeys(created_files + changed_files))
    if not touched:
        return "Model responded without file changes."
    if len(touched) <= 3:
        return f"Updated files: {', '.join(touched)}"
    return f"Updated {len(touched)} files."


def _resolve_target_project(task_project: str) -> str:
    """
    Resolves the absolute target project path.
    If the task_project is absolute, use it. Otherwise, fall back to config project_root or current dir.
    """
    if os.path.isabs(task_project):
        return task_project
    config = _load_config()
    project_root = config.get("project_root")
    if project_root and os.path.exists(project_root):
        return project_root
    return os.path.abspath(task_project)


def run_basic_quality_checks(project_root: str, affected_files: List[str]) -> Dict[str, any]:
    """
    Simple quality checks: py_compile on affected python files, optional pytest if available.
    """
    compile_errors: Dict[str, str] = {}
    for rel in affected_files:
        if not rel.endswith(".py"):
            continue
        abs_path = os.path.join(project_root, rel)
        if not os.path.exists(abs_path):
            continue
        try:
            subprocess.run(
                ["python", "-m", "py_compile", abs_path],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            compile_errors[rel] = exc.stderr or exc.stdout or "py_compile failed"

    tests_run = False
    tests_status = "skipped"
    tests_output = ""
    tests_dir = os.path.join(project_root, "tests")
    if os.path.isdir(tests_dir):
        try:
            tests_run = True
            proc = subprocess.run(
                ["python", "-m", "pytest", tests_dir, "-q"],
                capture_output=True,
                text=True,
                check=False,
            )
            tests_output = proc.stdout + "\n" + proc.stderr
            tests_status = "ok" if proc.returncode == 0 else "error"
        except Exception as exc:
            tests_output = str(exc)
            tests_status = "error"

    return {
        "compile_errors": compile_errors,
        "tests_run": tests_run,
        "tests_status": tests_status,
        "tests_output": tests_output,
    }


def run_task(task_id_or_path: str) -> Dict:
    """
    Executes a single task (by TASK_ID or path) through Meta-Agent pipeline with safety and quality checks.
    """
    _ensure_dir(TASKS_DIR)
    _ensure_dir(REPORTS_DIR)
    _ensure_dir(PATCHES_DIR)

    started_at = datetime.utcnow().isoformat() + "Z"
    finished_at: str | None = None
    model_name: str | None = None

    report: Report | None = None
    json_path = None
    md_path = None

    try:
        task = load_task(task_id_or_path)
        target_project = _resolve_target_project(task.project)

        prompt_metadata = {
            "task_id": task.task_id,
            "project": task.project,
            "task_type": task.task_type,
            "title": task.title,
            "priority": task.priority,
            "source": task.source,
            "target_project": target_project,
            "run_mode": "task",
        }

        context = ProjectScanner(target_project).collect_project_files()
        full_prompt = PromptBuilder().build_prompt(task.body_markdown, context, prompt_metadata)

        client = CodexClient()
        model_name = client.model
        response = client.send(full_prompt)

        if isinstance(response, str) and response.lstrip().startswith("[ERROR]"):
            raise RuntimeError(response)

        # Build change set from model output
        change_set = build_change_set_from_response(target_project, response)

        # Evaluate safety
        policy = load_safety_policy()
        safety_eval = evaluate_change_set(policy, change_set)

        # Apply changes (or write patches)
        apply_result = {
            "changed_files": [],
            "created_files": [],
            "deleted_files": [],
            "patch_files": [],
        }
        if safety_eval.overall_verdict == "block":
            apply_result = {
                "changed_files": [],
                "created_files": [],
                "deleted_files": [],
                "patch_files": [],
            }
            status = "blocked"
            error_message = "Changes blocked by safety policy."
        else:
            status = "ok"
            error_message = None
            if safety_eval.write_mode == "patch_only":
                apply_result = write_change_set_as_patches(change_set, PATCHES_DIR)
            else:
                apply_result = apply_change_set_direct(change_set)

        # Quality checks
        qc_result = run_basic_quality_checks(
            target_project,
            (apply_result.get("changed_files") or []) + (apply_result.get("created_files") or []),
        )

        risks: List[str] = []
        notes: List[str] = []
        if qc_result.get("compile_errors"):
            risks.append("Compile errors detected in changed python files.")
            status = "partial" if status == "ok" else status
        if qc_result.get("tests_status") == "error":
            risks.append("Tests failed.")
            status = "partial" if status == "ok" else status

        safety_status = safety_eval.overall_verdict
        blocked_files = [f.path for f in safety_eval.files if f.verdict == "block"]
        warning_files = [f.path for f in safety_eval.files if f.verdict == "warn"]
        patch_files = apply_result.get("patch_files") or []

        finished_at = datetime.utcnow().isoformat() + "Z"
        report = Report(
            task_id=task.task_id,
            project=task.project,
            task_type=task.task_type,
            title=task.title,
            priority=task.priority,
            status=status,
            error_message=error_message,
            summary=_build_summary(status, apply_result.get("changed_files", []), apply_result.get("created_files", []), error_message),
            changed_files=apply_result.get("changed_files", []),
            created_files=apply_result.get("created_files", []),
            deleted_files=apply_result.get("deleted_files", []),
            risks=risks,
            notes=notes,
            safety_status=safety_status,
            blocked_files=blocked_files,
            warning_files=warning_files,
            patch_files=patch_files,
            meta={
                "started_at": started_at,
                "finished_at": finished_at,
                "model": model_name,
                "source": task.source,
                "task_path": task.path,
                "target_project": target_project,
                "task_type": task.task_type,
                "priority": task.priority,
                "write_mode_used": safety_eval.write_mode,
                "safety_reasons": safety_eval.reasons,
                "quality_checks": qc_result,
            },
        )
    except (TaskParseError, FileNotFoundError) as exc:
        finished_at = datetime.utcnow().isoformat() + "Z"
        report = Report(
            task_id=getattr(exc, "task_id", task_id_or_path),
            project="unknown",
            task_type="unknown",
            title="",
            priority="normal",
            status="error",
            error_message=str(exc),
            summary=_build_summary("error", [], [], str(exc)),
            changed_files=[],
            created_files=[],
            deleted_files=[],
            safety_status="block",
            blocked_files=[],
            warning_files=[],
            patch_files=[],
            meta={
                "started_at": started_at,
                "finished_at": finished_at,
                "model": model_name,
                "source": "unknown",
                "task_path": task_id_or_path,
            },
        )
    except Exception as exc:
        finished_at = datetime.utcnow().isoformat() + "Z"
        report = Report(
            task_id=getattr(exc, "task_id", str(task_id_or_path)),
            project=getattr(locals().get("task", None), "project", "unknown"),
            task_type=getattr(locals().get("task", None), "task_type", "unknown"),
            title=getattr(locals().get("task", None), "title", ""),
            priority=getattr(locals().get("task", None), "priority", "normal"),
            status="error",
            error_message=str(exc),
            summary=_build_summary("error", [], [], str(exc)),
            changed_files=[],
            created_files=[],
            deleted_files=[],
            safety_status="block",
            blocked_files=[],
            warning_files=[],
            patch_files=[],
            meta={
                "started_at": started_at,
                "finished_at": finished_at,
                "model": model_name,
                "source": getattr(locals().get("task", None), "source", "unknown"),
                "task_path": getattr(locals().get("task", None), "path", task_id_or_path),
            },
        )
    finally:
        if report is not None:
            finished = finished_at or datetime.utcnow().isoformat() + "Z"
            report.meta.setdefault("finished_at", finished)
            report.meta.setdefault("started_at", started_at)
            try:
                from datetime import timezone

                started_dt = datetime.fromisoformat(report.meta["started_at"].replace("Z", "+00:00"))
                finished_dt = datetime.fromisoformat(report.meta["finished_at"].replace("Z", "+00:00"))
                duration_sec = max((finished_dt - started_dt).total_seconds(), 0)
                report.meta["duration_sec"] = duration_sec
            except Exception:
                pass

            json_path = write_json_report(report)
            md_path = write_md_report(report)

    return {
        "task_id": report.task_id if report else task_id_or_path,
        "project": report.project if report else "unknown",
        "task_type": report.task_type if report else "unknown",
        "title": report.title if report else "",
        "priority": report.priority if report else "normal",
        "status": report.status if report else "error",
        "error_message": report.error_message if report else "unknown error",
        "changed_files": report.changed_files if report else [],
        "created_files": report.created_files if report else [],
        "deleted_files": report.deleted_files if report else [],
        "summary": report.summary if report else "",
        "risks": report.risks if report else [],
        "notes": report.notes if report else [],
        "safety_status": report.safety_status if report else "block",
        "blocked_files": report.blocked_files if report else [],
        "warning_files": report.warning_files if report else [],
        "patch_files": report.patch_files if report else [],
        "meta": report.meta if report else {},
        "report_json_path": json_path,
        "report_md_path": md_path,
    }
