import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple

import yaml
from approval_engine import ApprovalError, approve_apply
from control_center_server import run_server
from file_manager import build_change_set_from_response
from llm_client import LLMClient
from logger import configure_logger
from meta_core import run_task
from offmarket_scheduler import main as scheduler_main
from offmarket_scheduler import status as scheduler_status
from paths import (BASE_DIR, OUTPUT_DIR, PATCHES_DIR, PROMPTS_ARCHIVE_DIR,
                   PROMPTS_DIR, REPORTS_DIR, STAGES_PATH, TASKS_DIR)
from project_scanner import ProjectScanner
from projects_config import load_project_registry, resolve_project_root
from prompt_builder import PromptBuilder
from report_schema import Report, write_json_report, write_md_report
from run_lock import RunLock, describe_existing_lock, resolve_lock_path
from safety_policy import load_safety_policy
from schedule_contract import ScheduleValidationError
from secret_masking import mask_secrets
from task_contract import TaskConstraints, TaskContext, TaskLLM, TaskSpec
from version import __version__
from watch import process_inbox_once
from write_engine import apply_change_set_with_policy

try:
    from supervisor_runner import run_supervisor_cycle
except Exception:
    run_supervisor_cycle = None
from task_archiver import archive_task_file
from task_manager import list_tasks

FRONT_MATTER_DELIMITER = "---"
ALLOWED_MODES = {"readonly", "write_dev", "write_prod"}
DEFAULT_TASK_FILE = os.path.join(TASKS_DIR, "task_current.md")
MAX_CONTEXT_CHARS = 250_000

try:
    from tools.qe_config import get_qe_paths
except Exception:  # pragma: no cover - fallback for legacy runs
    get_qe_paths = None


def _resolve_base_dir() -> str:
    env_root = os.getenv("QE_ROOT")
    if env_root:
        return env_root
    if get_qe_paths:
        try:
            return str(get_qe_paths()["qe_root"])
        except Exception:
            pass

    # In the src-layout, find root by looking for src/quantum_edge_core
    curr = Path(__file__).resolve()
    for _ in range(6):
        if (curr / "src" / "quantum_edge_core").is_dir():
            return str(curr)
        curr = curr.parent

    return BASE_DIR


def _resolve_runtime_dir() -> str:
    base = _resolve_base_dir()
    base_abs = os.path.abspath(base)
    env_runtime = os.getenv("META_AGENT_RUNTIME_DIR") or os.getenv("QE_RUNTIME_DIR")
    if env_runtime:
        candidate = os.path.abspath(env_runtime)
        try:
            if os.path.commonpath([candidate, base_abs]) == base_abs:
                return candidate
        except ValueError:
            pass
    return os.path.abspath(os.path.join(base_abs, "runtime"))


def _resolve_meta_config_path(path: Optional[str]) -> str:
    base = _resolve_base_dir()
    env_override = os.getenv("META_AGENT_CONFIG")
    candidate = env_override or path or "src/quantum_edge_core/config/meta_agent.yaml"
    if os.path.isabs(candidate):
        return candidate
    return os.path.abspath(os.path.join(base, candidate))


def load_task_from_file(path: str) -> Tuple[Dict, str]:
    """
    Returns (metadata, body_text) parsed from a task file with YAML front matter.
    If no front matter is present, metadata is an empty dict and body is the full file.
    """
    with open(path, "r", encoding="utf-8") as handle:
        content = handle.read()

    lines = content.splitlines()
    if not lines or lines[0].strip() != FRONT_MATTER_DELIMITER:
        return {}, content

    end_index = None
    for idx, line in enumerate(lines[1:], start=1):
        if line.strip() == FRONT_MATTER_DELIMITER:
            end_index = idx
            break

    if end_index is None:
        return {}, content

    metadata_text = "\n".join(lines[1:end_index])
    metadata = yaml.safe_load(metadata_text) or {}
    if not isinstance(metadata, dict):
        raise yaml.YAMLError("Front matter must be a mapping.")
    body = "\n".join(lines[end_index + 1 :]).strip()
    return metadata, body


def run_diag() -> int:
    results: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str) -> None:
        results.append((name, ok, detail))
        status = "PASS" if ok else "FAIL"
        print(f"{status} - {name}: {detail}")

    base_dir = Path(_resolve_base_dir())

    # Config
    cfg_path = Path(_resolve_meta_config_path(None))
    cfg_ok = cfg_path.exists() and os.access(cfg_path, os.R_OK)
    record(
        "config",
        cfg_ok,
        str(cfg_path) if cfg_ok else f"missing/unreadable ({cfg_path})",
    )

    # Projects registry
    try:
        registry = load_project_registry()
        missing = [
            pid
            for pid, info in registry.projects.items()
            if not info.root_path.exists()
        ]
        if missing:
            record("projects_registry", False, f"missing roots: {', '.join(missing)}")
        else:
            record("projects_registry", True, f"{len(registry.projects)} projects")
    except Exception as exc:
        record("projects_registry", False, f"{exc}")

    # Prompts
    prompts_ok = Path(PROMPTS_DIR).exists()
    if not prompts_ok:
        record("prompts", False, f"missing prompts dir: {PROMPTS_DIR}")
    else:
        stage_ok = True
        stage_detail = "ok"
        if os.path.exists(STAGES_PATH):
            try:
                with open(STAGES_PATH, "r", encoding="utf-8") as handle:
                    stages = yaml.safe_load(handle) or []
                for stage in stages:
                    prompt_file = stage.get("prompt")
                    if not prompt_file:
                        stage_ok = False
                        stage_detail = "stage missing prompt path"
                        break
                    resolved = Path(prompt_file)
                    if not resolved.is_absolute():
                        resolved = Path(STAGES_PATH).parent / prompt_file
                    if not resolved.exists():
                        fallback = Path(PROMPTS_DIR) / Path(prompt_file).name
                        if fallback.exists():
                            resolved = fallback
                        else:
                            stage_ok = False
                            stage_detail = f"missing prompt: {prompt_file}"
                            break
            except Exception as exc:
                stage_ok = False
                stage_detail = f"stages.yaml unreadable: {exc}"
        record("prompts", stage_ok, stage_detail)

    # Runtime dirs
    runtime_dir = Path(_resolve_runtime_dir()).resolve()
    runtime_checks = []
    for sub in ("reports", "patches", "logs", "runs"):
        path = runtime_dir / sub
        try:
            path.mkdir(parents=True, exist_ok=True)
            test_file = path / ".write_test"
            test_file.write_text("ok", encoding="utf-8")
            test_file.unlink(missing_ok=True)
            runtime_checks.append((sub, True, str(path)))
        except Exception as exc:
            runtime_checks.append((sub, False, f"{path} ({exc})"))
    for sub, ok, detail in runtime_checks:
        record(f"runtime_{sub}", ok, detail)

    # Safety config
    try:
        policy = load_safety_policy()
        ok = policy is not None
        record("safety_policy", ok, "loaded" if ok else "missing")
    except Exception as exc:
        record("safety_policy", False, f"{exc}")

    # Tracked secrets (minimal check)
    secret_suffixes = (".env", ".key", ".pem", ".pfx", ".p12", ".enc")
    try:
        proc = subprocess.run(
            ["git", "ls-files"],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(base_dir),
        )
        if proc.returncode != 0:
            record("tracked_secrets", False, "git ls-files failed")
        else:
            tracked = [
                line.strip() for line in proc.stdout.splitlines() if line.strip()
            ]
            hits = []
            for path in tracked:
                lower = path.lower()
                if lower.endswith((".example", ".sample", ".template")):
                    continue
                if any(x in lower for x in ("venv/", "venv\\", ".venv/", ".venv\\")):
                    continue
                if lower.endswith((".env.enc", "engine_defaults.env")):
                    continue
                if (
                    lower.endswith(secret_suffixes)
                    or "/secrets/" in lower
                    or "\\secrets\\" in lower
                ):
                    hits.append(path)
            record(
                "tracked_secrets",
                not hits,
                "none" if not hits else f"found: {', '.join(hits)}",
            )
    except Exception as exc:
        record("tracked_secrets", False, f"{exc}")

    all_ok = all(ok for _, ok, _ in results)
    return 0 if all_ok else 1


def _parse_global_args(argv: list[str]):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--runtime-dir", dest="runtime_dir")
    parser.add_argument("--log-level", dest="log_level")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_known_args(argv)


def _apply_global_args(args) -> None:
    if args.runtime_dir:
        os.environ["META_AGENT_RUNTIME_DIR"] = args.runtime_dir
    if args.log_level:
        os.environ["META_AGENT_LOG_LEVEL"] = args.log_level


def _sanitize_args(argv: list[str]) -> list[str]:
    sanitized: list[str] = []
    for arg in argv:
        sanitized.append(mask_secrets(arg))
    return sanitized


def _format_run_summary_json(report) -> str:
    payload = {
        "run_id": report.run_id,
        "verdict": report.verdict,
        "exit_code": report.exit_code,
        "report_path": report.artifacts.report_path,
        "patches_dir": report.artifacts.patches_dir,
    }
    return json.dumps(payload, ensure_ascii=True)


def _emit_run_summary(report, json_mode: bool, quiet: bool) -> None:
    if json_mode:
        print(_format_run_summary_json(report))
        return
    if quiet:
        return
    print(f"[INFO] run_id={report.run_id}")
    print(f"[INFO] verdict={report.verdict} exit_code={report.exit_code}")
    print(f"[INFO] report_path={report.artifacts.report_path}")
    print(f"[INFO] patches_dir={report.artifacts.patches_dir}")


def _is_transient_error(report) -> bool:
    if report.exit_code not in {30}:
        return False
    text = " ".join(report.errors or []).lower()
    transient_markers = [
        "timeout",
        "timed out",
        "rate limit",
        "temporarily",
        "connection",
        "unavailable",
        "429",
        "503",
    ]
    return any(marker in text for marker in transient_markers)


def _run_task_cli(
    task_path: str,
    json_mode: bool,
    quiet: bool,
    timeout_seconds: Optional[int],
    llm_timeout_seconds: Optional[int],
    retries: int,
    report_path: Optional[str],
    cli_args: list[str],
) -> int:
    runtime_dir = _resolve_runtime_dir()
    log_level = (os.getenv("META_AGENT_LOG_LEVEL") or "INFO").upper()
    logger = configure_logger("meta_agent.run_task", runtime_dir, log_level)

    cli_context = {
        "command": "run-task",
        "args_sanitized": _sanitize_args(cli_args),
    }

    attempt = 0
    report = None
    while attempt <= retries:
        attempt += 1
        if attempt > 1:
            backoff = min(2 ** (attempt - 2), 8)
            logger.info("Retrying run-task attempt=%s backoff=%ss", attempt, backoff)
            time.sleep(backoff)
        try:
            report = run_task(
                task_path,
                timeout_seconds=timeout_seconds,
                llm_timeout_seconds=llm_timeout_seconds,
                cli_context=cli_context,
            )
        except Exception as exc:
            logger.error("run_task raised exception: %s", exc)
            continue
        if report.exit_code == 0:
            break
        if not _is_transient_error(report):
            break

    if report is None:
        return 30

    if report_path:
        try:
            abs_report = os.path.abspath(report_path)
            os.makedirs(os.path.dirname(abs_report), exist_ok=True)
            base = _resolve_base_dir()
            src = os.path.join(base, report.artifacts.report_path)
            shutil.copy2(src, abs_report)
        except Exception as exc:
            logger.error("Failed to duplicate report: %s", exc)

    _emit_run_summary(report, json_mode=json_mode, quiet=quiet)
    return report.exit_code


class MetaAgent:
    def __init__(self, config_path: str = "config.json"):
        resolved = _resolve_meta_config_path(config_path)
        self.config = self._load_config(resolved)
        self.builder = PromptBuilder()
        self.mode = self._resolve_mode()
        self.client = LLMClient(
            provider=self.config.get("provider"),
            mode=self.mode,
            model=self.config.get("model"),
        )
        self.lock_busy = False
        # Architect Mode: default to global project root
        self.project_root = self.config.get("project_root") or _resolve_base_dir()
        projects_path = (self.config or {}).get("projects_path")
        self.project_registry = (
            load_project_registry(projects_path)
            if projects_path
            else load_project_registry()
        )

    def _load_config(self, path: str) -> Dict:
        if not os.path.exists(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as handle:
                if path.lower().endswith((".yaml", ".yml")):
                    return yaml.safe_load(handle) or {}
                return json.load(handle)
        except (json.JSONDecodeError, yaml.YAMLError, OSError):
            return {}

    def _resolve_mode(self) -> str:
        """
        Determines execution mode (dev/prod) based on env or config.
        """
        env_mode = os.getenv("META_AGENT_MODE")
        cfg_mode = (self.config or {}).get("mode")
        mode = (env_mode or cfg_mode or "dev").strip().lower()
        if mode not in {"dev", "prod"}:
            print(f"[WARN] Unsupported mode '{mode}', defaulting to dev.")
            mode = "dev"
        return mode

    def _resolve_output_path(self, output_path: str | None, task_id: str) -> str:
        if output_path:
            dest = os.path.abspath(output_path)
        else:
            dest = os.path.abspath(os.path.join("output", f"{task_id}_response.md"))

        # Keep outputs inside the Meta-Agent directory for predictability.
        meta_root = os.path.abspath(os.getcwd())
        try:
            common = os.path.commonpath([dest, meta_root])
        except ValueError:
            common = ""

        if common != meta_root:
            print(
                "[WARN] output_path is outside the Meta-Agent directory; redirecting to output/."
            )
            dest = os.path.abspath(os.path.join("output", os.path.basename(dest)))

        return dest

    def _load_stages(self, path: str = STAGES_PATH):
        if not os.path.exists(path):
            print(f"[ERROR] stages file not found: {path}")
            return []
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return yaml.safe_load(handle) or []
        except yaml.YAMLError as exc:
            print(f"[ERROR] Failed to parse stages file: {exc}")
            return []

    def run_stage_pipeline(
        self, override_project_id: Optional[str] = None
    ) -> tuple[bool, list]:
        print("[INFO] Starting stage pipeline (Architect Mode)...")
        stages = self._load_stages()
        if not stages:
            print("[WARN] No stages to run.")
            return False, []

        lock_path = resolve_lock_path(_resolve_base_dir())
        lock = RunLock(lock_path)
        if not lock.acquire():
            detail = describe_existing_lock(lock_path)
            msg = "Meta-Agent is already running (lock held)."
            if detail:
                msg = f"{msg} {detail}"
            print(f"[ERROR] {msg}")
            self.lock_busy = True
            return False, stages

        try:
            for stage in stages:
                name = stage.get("name", "unnamed_stage")
                prompt_file = stage.get("prompt")
                if not prompt_file:
                    print(f"[ERROR] Stage {name} is missing a prompt path.")
                    return False, stages

                # Operating on the entire repository as a single Monorepo (Architect Mode)
                target_project = self.project_root
                project_id = "monorepo"

                if not os.path.isdir(target_project):
                    print(
                        f"[ERROR] Target repository root does not exist: {target_project}"
                    )
                    return False, stages

                if os.path.isabs(prompt_file):
                    resolved_prompt = prompt_file
                else:
                    stage_base = os.path.dirname(STAGES_PATH)
                    resolved_prompt = os.path.abspath(
                        os.path.join(stage_base, prompt_file)
                    )
                if not os.path.exists(resolved_prompt):
                    alternative = os.path.abspath(
                        os.path.join(PROMPTS_DIR, os.path.basename(prompt_file))
                    )
                    if os.path.exists(alternative):
                        resolved_prompt = alternative
                    else:
                        print(
                            f"[ERROR] Prompt file not found for stage {name}: {prompt_file}"
                        )
                        return False, stages

                print(f"[INFO] Running stage: {name} using {prompt_file}")
                try:
                    with open(resolved_prompt, "r", encoding="utf-8") as handle:
                        stage_instructions = handle.read()

                    print(
                        f"[INFO] Collecting global project context for stage {name}..."
                    )
                    scanner = ProjectScanner(target_project)

                    # Global Architect Mode: Gather Tree + All Source
                    tree_view = scanner.get_project_structure()
                    source_context = scanner.read_all_code()

                    print(
                        f"[INFO] Collected context for stage {name} (Architect Mode)."
                    )

                    system_prompt = (
                        "You are a Global Architect. You have full vision of the project structure and source code.\n"
                        f"PROJECT STRUCTURE:\n{tree_view}\n\n"
                        "Use this context to fulfill the user's request precisely. "
                        "Output only file blocks using the format ===FILE: path === followed by the new content."
                    )

                    full_prompt = self.builder.build_prompt(
                        stage_instructions,
                        f"SOURCE CODE:\n{source_context}",
                        {
                            "stage": name,
                            "mode": "architect",
                            "target_project": target_project,
                            "project_id": project_id,
                            "project_path": target_project,
                        },
                    )

                    print(
                        f"[INFO] Sending prompt to LLM for stage {name} (Architect Mode)..."
                    )
                    response = self.client.send(
                        full_prompt, system_prompt=system_prompt
                    )
                    print(f"[INFO] LLM response received for stage {name}.")

                    if isinstance(response, str) and response.lstrip().startswith(
                        "[ERROR]"
                    ):
                        print(f"[ERROR] LLM call failed for stage {name}: {response}")
                        return False, stages

                    change_set = build_change_set_from_response(
                        target_project, response
                    )

                    safety_mode = (self.config.get("safety") or {}).get(
                        "mode", "manual"
                    )
                    force_direct = safety_mode == "auto"

                    outcome = apply_change_set_with_policy(
                        change_set, PATCHES_DIR, force_direct=force_direct
                    )

                    if outcome.applied:
                        for f in outcome.created_files:
                            print(f"[INFO] Successfully wrote to {f}")
                        for f in outcome.changed_files:
                            print(f"[INFO] Successfully wrote to {f}")

                    started_at = datetime.utcnow().isoformat() + "Z"
                    finished_at = datetime.utcnow().isoformat() + "Z"
                    summary = (
                        f"Stage {name} completed with {len(outcome.changed_files) + len(outcome.created_files)} file changes."
                        if outcome.changed_files or outcome.created_files
                        else f"Stage {name} completed with no file changes."
                    )
                    report = Report(
                        task_id=f"stage_{name}",
                        project=project_id,
                        task_type="stage",
                        title=name,
                        priority="normal",
                        status=outcome.status,
                        error_message=outcome.error_message,
                        summary=summary,
                        changed_files=outcome.changed_files,
                        created_files=outcome.created_files,
                        deleted_files=outcome.deleted_files,
                        safety_status=outcome.safety_eval.overall_verdict,
                        blocked_files=[
                            f.path
                            for f in outcome.safety_eval.files
                            if f.verdict == "block"
                        ],
                        warning_files=[
                            f.path
                            for f in outcome.safety_eval.files
                            if f.verdict == "warn"
                        ],
                        patch_files=outcome.patch_files,
                        meta={
                            "started_at": started_at,
                            "finished_at": finished_at,
                            "model": getattr(self.client, "model", None),
                            "source": resolved_prompt,
                            "task_path": resolved_prompt,
                            "target_project": target_project,
                            "stage": name,
                            "write_mode_used": outcome.write_mode_used,
                            "safety_reasons": outcome.safety_eval.reasons,
                        },
                    )
                    write_json_report(report)
                    write_md_report(report)
                    print(
                        f"[INFO] Stage {name} completed with status={outcome.status}."
                    )
                except Exception as exc:
                    print(f"[ERROR] Stage {name} failed: {exc}")
                    return False, stages
        finally:
            lock.release()

        return True, stages

    def run_task_file(self, task_path: str) -> bool:
        """
        Legacy wrapper that routes task execution through meta_core.run_task.
        """
        report = run_task(task_path)
        if report.exit_code != 0:
            print(
                f"[ERROR] Task {report.task_id} failed: {', '.join(report.errors) if report.errors else report.summary}"
            )
            return False

        print(f"[INFO] Task {report.task_id} completed.")
        if report.summary:
            print(f"[INFO] Summary: {report.summary}")
        print(f"[INFO] Report: {report.artifacts.report_path}")
        try:
            archive_task_file(os.path.abspath(task_path), target_project=None)
            print(f"[INFO] Archived task file: {task_path}")
        except Exception as exc:
            print(f"[WARN] Failed to archive task file {task_path}: {exc}")
        return True


def parse_args(argv: Optional[list[str]] = None):
    parser = argparse.ArgumentParser(description="Meta-Agent CLI")
    parser.add_argument(
        "--config", dest="config_path", help="Path to meta-agent config (YAML/JSON)."
    )
    parser.add_argument(
        "--mode",
        default="auto",
        help="Execution mode (stages|task) or supervisor cadence (daily|weekly|adhoc|auto).",
    )
    parser.add_argument(
        "--task", dest="task_path", help="Path to a .md task file for task mode."
    )
    parser.add_argument(
        "--task-id",
        dest="task_id",
        help="Task ID to resolve in tasks/<ID>.md for task mode.",
    )
    parser.add_argument(
        "--task-file", dest="task_file", help="Alias for --task (legacy)."
    )
    parser.add_argument(
        "--list-tasks",
        action="store_true",
        help="List available tasks from tasks/ directory.",
    )
    parser.add_argument(
        "--project", dest="filter_project", help="Filter tasks by project when listing."
    )
    parser.add_argument(
        "--task-type",
        dest="filter_task_type",
        help="Filter tasks by task type when listing.",
    )
    parser.add_argument(
        "--supervisor-goal",
        dest="supervisor_goal",
        help="Run a supervisor goal (high-level string).",
    )
    parser.add_argument(
        "--supervisor-project",
        dest="supervisor_project",
        help="Project root for supervisor goal runs.",
        default="ai_scalper_bot",
    )
    parser.add_argument(
        "--project-id",
        dest="stage_project_id",
        help="Override project id for stage pipeline.",
    )
    parser.add_argument(
        "--once", action="store_true", help="Run once and exit (default behavior)."
    )
    return parser.parse_args(argv)


def parse_run_task_args(argv: list[str]):
    parser = argparse.ArgumentParser(description="Run a TaskSpec")
    parser.add_argument(
        "--task",
        required=True,
        help="Path to task.yaml or task.md OR a natural language instruction string.",
    )
    parser.add_argument("--timeout-seconds", type=int, dest="timeout_seconds")
    parser.add_argument("--llm-timeout-seconds", type=int, dest="llm_timeout_seconds")
    parser.add_argument("--retries", type=int, default=0)
    parser.add_argument("--report-path", dest="report_path")
    return parser.parse_args(argv)


def parse_create_task_args(argv: list[str]):
    parser = argparse.ArgumentParser(description="Create a TaskSpec skeleton")
    parser.add_argument("--project", required=True, help="Project id from registry")
    parser.add_argument("--objective", required=True, help="Short objective")
    parser.add_argument("--output", help="Destination path for task.yaml")
    return parser.parse_args(argv)


def _create_task_spec_file(
    project_id: str, objective: str, output_path: Optional[str]
) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    task_id = f"T{stamp}_{project_id}"
    created_at = datetime.now(timezone.utc).isoformat()
    spec = TaskSpec(
        task_id=task_id,
        created_at=created_at,
        project_id=project_id,
        project_root=None,
        objective=objective,
        instructions="Describe the task requirements here.",
        constraints=TaskConstraints(),
        context=TaskContext(),
        llm=TaskLLM(),
        mode="task",
        metadata={},
    )

    if output_path:
        dest = os.path.abspath(output_path)
    else:
        runtime_dir = os.getenv("QE_RUNTIME_DIR") or os.path.join(
            _resolve_base_dir(), "runtime"
        )
        inbox_dir = os.path.join(runtime_dir, "inbox")
        os.makedirs(inbox_dir, exist_ok=True)
        dest = os.path.join(inbox_dir, f"{task_id}.yaml")

    with open(dest, "w", encoding="utf-8") as handle:
        yaml.safe_dump(spec.to_dict(), handle, allow_unicode=True, sort_keys=False)
    return dest


def parse_status_args(argv: list[str]):
    parser = argparse.ArgumentParser(description="Show recent runs")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--last", action="store_true")
    return parser.parse_args(argv)


def parse_health_args(argv: list[str]):
    parser = argparse.ArgumentParser(description="Health check")
    return parser.parse_args(argv)


def parse_scheduler_args(argv: list[str]):
    parser = argparse.ArgumentParser(description="Run off-market scheduler")
    parser.add_argument("--schedules-dir", dest="schedules_dir")
    parser.add_argument("--tick-seconds", type=int, default=2)
    parser.add_argument("--once", action="store_true")
    return parser.parse_args(argv)


def parse_scheduler_status_args(argv: list[str]):
    parser = argparse.ArgumentParser(description="Scheduler status")
    parser.add_argument("--schedules-dir", dest="schedules_dir")
    return parser.parse_args(argv)


def parse_watch_args(argv: list[str]):
    parser = argparse.ArgumentParser(description="Watch inbox for TaskSpecs")
    parser.add_argument("--inbox", default="runtime/inbox")
    parser.add_argument("--poll-seconds", type=int, default=2)
    parser.add_argument("--archive", default="runtime/inbox_done")
    parser.add_argument("--failed", default="runtime/inbox_failed")
    return parser.parse_args(argv)


def parse_ui_args(argv: list[str]):
    parser = argparse.ArgumentParser(description="Run Control Center UI")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--token", dest="token")
    return parser.parse_args(argv)


def parse_approve_args(argv: list[str]):
    parser = argparse.ArgumentParser(description="Approve and apply a warn run")
    parser.add_argument("--run-id", required=True)
    return parser.parse_args(argv)


def parse_dump_run_args(argv: list[str]):
    parser = argparse.ArgumentParser(description="Dump run summary")
    parser.add_argument("--run-id", required=True)
    return parser.parse_args(argv)


def _resolve_path_under_base(path: str) -> str:
    base = _resolve_base_dir()
    base_abs = os.path.abspath(base)
    candidate = path if os.path.isabs(path) else os.path.join(base_abs, path)
    candidate = os.path.abspath(candidate)
    if os.path.commonpath([candidate, base_abs]) != base_abs:
        raise ValueError("Path escapes repo root")
    return candidate


def _status_cli(limit: int, show_last: bool, json_mode: bool, quiet: bool) -> int:
    runtime_dir = _resolve_runtime_dir()
    runs_dir = os.path.join(runtime_dir, "runs")
    if not os.path.isdir(runs_dir):
        if not quiet:
            print("[WARN] No runs directory found.")
        return 1

    reports: list[dict] = []
    for entry in sorted(os.listdir(runs_dir)):
        report_path = os.path.join(runs_dir, entry, "report.json")
        if not os.path.exists(report_path):
            continue
        try:
            with open(report_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            reports.append(data)
        except Exception:
            continue

    reports.sort(key=lambda r: r.get("finished_at", ""), reverse=True)
    if show_last and reports:
        last = reports[0]
        if json_mode:
            payload = {
                "run_id": last.get("run_id"),
                "verdict": last.get("verdict"),
                "exit_code": last.get("exit_code"),
                "finished_at": last.get("finished_at"),
                "report_path": last.get("artifacts", {}).get("report_path"),
            }
            print(json.dumps(payload, ensure_ascii=True))
        elif not quiet:
            print(
                f"[INFO] run_id={last.get('run_id')} verdict={last.get('verdict')} exit_code={last.get('exit_code')}"
            )
            print(f"[INFO] finished_at={last.get('finished_at')}")
            print(f"[INFO] report_path={last.get('artifacts', {}).get('report_path')}")
        return 0

    if json_mode:
        payload = [
            {
                "run_id": r.get("run_id"),
                "finished_at": r.get("finished_at"),
                "verdict": r.get("verdict"),
                "exit_code": r.get("exit_code"),
            }
            for r in reports[:limit]
        ]
        print(json.dumps(payload, ensure_ascii=True))
        return 0

    if not quiet:
        for rep in reports[:limit]:
            print(
                f"{rep.get('run_id')} {rep.get('finished_at')} verdict={rep.get('verdict')} exit_code={rep.get('exit_code')}"
            )
    return 0


def _health_check(runtime_dir: str) -> tuple[bool, list[tuple[str, bool, str]]]:
    results: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str) -> None:
        results.append((name, ok, detail))

    try:
        os.makedirs(runtime_dir, exist_ok=True)
        record("runtime_dir", True, runtime_dir)
    except Exception as exc:
        record("runtime_dir", False, str(exc))

    for sub in ("logs", "runs"):
        path = os.path.join(runtime_dir, sub)
        try:
            os.makedirs(path, exist_ok=True)
            record(f"runtime_{sub}", True, path)
        except Exception as exc:
            record(f"runtime_{sub}", False, str(exc))

    try:
        from safety_policy import load_safety_policy

        load_safety_policy()
        record("safety_policy", True, "loaded")
    except Exception as exc:
        record("safety_policy", False, str(exc))

    try:
        from write_engine import apply_change_set_with_policy  # noqa: F401

        record("write_engine", True, "available")
    except Exception as exc:
        record("write_engine", False, str(exc))

    ok = all(item[1] for item in results)
    return ok, results


def _health_cli(json_mode: bool, quiet: bool) -> int:
    runtime_dir = _resolve_runtime_dir()
    ok, results = _health_check(runtime_dir)
    if json_mode:
        payload = {
            "ok": ok,
            "checks": [
                {"name": name, "ok": passed, "detail": detail}
                for name, passed, detail in results
            ],
        }
        print(json.dumps(payload, ensure_ascii=True))
        return 0 if ok else 1
    if not quiet:
        for name, passed, detail in results:
            status = "PASS" if passed else "FAIL"
            print(f"{status} - {name}: {detail}")
    return 0 if ok else 1


def _watch_cli(args, json_mode: bool, quiet: bool) -> int:
    runtime_dir = _resolve_runtime_dir()
    log_level = (os.getenv("META_AGENT_LOG_LEVEL") or "INFO").upper()
    logger = configure_logger("meta_agent.watch", runtime_dir, log_level)

    try:
        inbox = _resolve_path_under_base(args.inbox)
        archive = _resolve_path_under_base(args.archive)
        failed = _resolve_path_under_base(args.failed)
    except ValueError as exc:
        if not quiet:
            print(f"[ERROR] {exc}")
        return 1

    while True:
        result = process_inbox_once(
            inbox=inbox,
            archive=archive,
            failed=failed,
            logger=logger,
        )
        if result.get("stop"):
            if not quiet:
                print("[INFO] STOP detected; exiting.")
            return 0
        if result.get("pause"):
            time.sleep(args.poll_seconds)
            continue
        time.sleep(args.poll_seconds)


def cleanup_after_successful_run(stages: list) -> None:
    """
    Archives prompt files, clears stages.yaml, and moves reports into output/.
    """
    os.makedirs(PROMPTS_ARCHIVE_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Archive prompt files used in this run
    for stage in stages:
        prompt_path = stage.get("prompt")
        if not prompt_path:
            continue
        if not os.path.isabs(prompt_path):
            prompt_path = os.path.join(BASE_DIR, prompt_path)
        if not os.path.exists(prompt_path):
            continue
        dest = os.path.join(PROMPTS_ARCHIVE_DIR, os.path.basename(prompt_path))
        try:
            if os.path.exists(dest):
                os.remove(dest)
            shutil.move(prompt_path, dest)
            print(f"[INFO] Archived prompt: {dest}")
        except Exception as exc:
            print(f"[WARN] Failed to archive prompt {prompt_path}: {exc}")

    # Clear stages.yaml
    try:
        with open(STAGES_PATH, "w", encoding="utf-8") as handle:
            handle.write("[]\n")
        print("[INFO] Cleared stages.yaml")
    except Exception as exc:
        print(f"[WARN] Failed to clear stages.yaml: {exc}")

    # Move reports into output/
    if os.path.isdir(REPORTS_DIR):
        for root, _, files in os.walk(REPORTS_DIR):
            rel_root = os.path.relpath(root, REPORTS_DIR)
            for filename in files:
                src = os.path.join(root, filename)
                dest_dir = (
                    os.path.join(OUTPUT_DIR, rel_root)
                    if rel_root != "."
                    else OUTPUT_DIR
                )
                os.makedirs(dest_dir, exist_ok=True)
                dest = os.path.join(dest_dir, filename)
                try:
                    if os.path.exists(dest):
                        os.remove(dest)
                    shutil.move(src, dest)
                    print(f"[INFO] Moved report: {dest}")
                except Exception as exc:
                    print(f"[WARN] Failed to move report {src}: {exc}")


def main() -> int:
    global_args, remaining = _parse_global_args(sys.argv[1:])
    _apply_global_args(global_args)

    cmd = remaining[0] if remaining else None
    if cmd == "version":
        if not global_args.quiet:
            print(__version__)
        return 0
    if cmd == "diag":
        return run_diag()
    if cmd == "run-task":
        args = parse_run_task_args(remaining[1:])
        return _run_task_cli(
            args.task,
            json_mode=global_args.json,
            quiet=global_args.quiet,
            timeout_seconds=args.timeout_seconds,
            llm_timeout_seconds=args.llm_timeout_seconds,
            retries=args.retries,
            report_path=args.report_path,
            cli_args=remaining[1:],
        )
    if cmd == "create-task":
        args = parse_create_task_args(remaining[1:])
        registry = load_project_registry()
        if args.project not in registry.projects:
            print(f"[ERROR] Unknown project id '{args.project}'.")
            return 1
        dest = _create_task_spec_file(args.project, args.objective, args.output)
        if not global_args.quiet:
            print(f"[INFO] TaskSpec created at {dest}")
        return 0
    if cmd == "status":
        args = parse_status_args(remaining[1:])
        return _status_cli(
            args.limit, args.last, json_mode=global_args.json, quiet=global_args.quiet
        )
    if cmd == "health":
        parse_health_args(remaining[1:])
        return _health_cli(json_mode=global_args.json, quiet=global_args.quiet)
    if cmd == "run-scheduler":
        args = parse_scheduler_args(remaining[1:])
        return scheduler_main(
            schedules_dir=args.schedules_dir,
            tick_seconds=args.tick_seconds,
            once=args.once,
            json_mode=global_args.json,
            quiet=global_args.quiet,
        )
    if cmd == "scheduler-status":
        args = parse_scheduler_status_args(remaining[1:])
        try:
            payload = scheduler_status(
                args.schedules_dir or None, _resolve_runtime_dir()
            )
        except ScheduleValidationError as exc:
            if not global_args.quiet:
                print(f"[ERROR] {exc}")
            return 1
        if global_args.json:
            print(json.dumps(payload, ensure_ascii=True))
            return 0
        if not global_args.quiet:
            for entry in payload:
                print(
                    f"{entry.get('schedule_id')} enabled={entry.get('enabled')} in_window={entry.get('in_window')} "
                    f"next_eligible_at={entry.get('next_eligible_at')} last_exit_code={entry.get('last_exit_code')} "
                    f"attempts={entry.get('attempts')}"
                )
        return 0
    if cmd == "watch":
        args = parse_watch_args(remaining[1:])
        return _watch_cli(args, json_mode=global_args.json, quiet=global_args.quiet)
    if cmd == "ui":
        args = parse_ui_args(remaining[1:])
        server, token = run_server(args.bind, args.port, token=args.token)
        if not global_args.quiet:
            print(f"[INFO] Control Center listening on http://{args.bind}:{args.port}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            return 0
        return 0
    if cmd == "approve-apply":
        args = parse_approve_args(remaining[1:])
        try:
            result = approve_apply(
                args.run_id, runtime_dir=_resolve_runtime_dir(), method="cli"
            )
        except ApprovalError as exc:
            if not global_args.quiet:
                print(f"[ERROR] {exc}")
            return exc.exit_code
        if not global_args.quiet:
            print(
                f"[INFO] approve_apply exit_code={result.get('exit_code')} applied={result.get('applied')}"
            )
        return int(result.get("exit_code") or 0)
    if cmd == "dump-run":
        args = parse_dump_run_args(remaining[1:])
        runtime_dir = _resolve_runtime_dir()
        report_path = os.path.join(runtime_dir, "runs", args.run_id, "report.json")
        if not os.path.exists(report_path):
            if not global_args.quiet:
                print(f"[ERROR] report.json not found for run_id={args.run_id}")
            return 1
        with open(report_path, "r", encoding="utf-8") as handle:
            report = json.load(handle)
        payload = {
            "run_id": report.get("run_id"),
            "verdict": report.get("verdict"),
            "exit_code": report.get("exit_code"),
            "applied": (report.get("changes") or {}).get("applied"),
            "gates_passed": (report.get("gates") or {}).get("passed"),
            "report_path": (report.get("artifacts") or {}).get("report_path"),
            "patches_dir": (report.get("artifacts") or {}).get("patches_dir"),
            "gates_dir": "runtime/runs/{}/gates".format(args.run_id),
        }
        if global_args.json:
            print(json.dumps(payload, ensure_ascii=True))
        elif not global_args.quiet:
            print(
                f"run_id={payload['run_id']} verdict={payload['verdict']} exit_code={payload['exit_code']}"
            )
            print(
                f"applied={payload['applied']} gates_passed={payload['gates_passed']}"
            )
            print(f"report_path={payload['report_path']}")
            print(f"patches_dir={payload['patches_dir']}")
        return 0

    args = parse_args(remaining)
    if args.config_path:
        os.environ["META_AGENT_CONFIG"] = args.config_path

    if args.once:
        print("[INFO] --once specified; running a single pass.")

    if args.list_tasks:
        try:
            tasks = list_tasks(
                project=args.filter_project, task_type=args.filter_task_type
            )
        except Exception as exc:
            print(f"[ERROR] Failed to list tasks: {exc}")
            return 1
        if not tasks:
            print("No tasks found.")
            return 0
        header = f"{'TASK_ID':30} {'PROJECT':18} {'TYPE':14} TITLE"
        print(header)
        print("-" * len(header))
        for task in tasks:
            print(
                f"{task.task_id:30} {task.project:18} {task.task_type:14} {task.title}"
            )
        return 0

    if args.supervisor_goal:
        if run_supervisor_cycle is None:
            print("[ERROR] supervisor_runner.run_supervisor_cycle is unavailable.")
            return 1
        try:
            sup_result = run_supervisor_cycle(
                goal=args.supervisor_goal,
                mode=args.mode or "daily",
                project=getattr(args, "supervisor_project", None) or "ai_scalper_bot",
            )
        except Exception as exc:
            print(f"[ERROR] Supervisor run failed: {exc}")
            return 1

        ok_count = sum(
            1 for r in sup_result.get("tasks", []) if r.get("status") == "ok"
        )
        err_count = sum(
            1 for r in sup_result.get("tasks", []) if r.get("status") == "error"
        )
        partial_count = sum(
            1 for r in sup_result.get("tasks", []) if r.get("status") == "partial"
        )

        print("[INFO] Supervisor run completed.")
        print(f"  Goal: {sup_result.get('goal')}")
        print(f"  Mode: {sup_result.get('mode')}")
        print(f"  Project: {sup_result.get('project')}")
        print(f"  Status: {sup_result.get('status')}")
        print(
            f"  Tasks total: {len(sup_result.get('tasks', []))} (ok/partial/error: {ok_count}/{partial_count}/{err_count})"
        )
        if sup_result.get("supervisor_md_path"):
            print(f"  Summary (MD): {sup_result.get('supervisor_md_path')}")
        if sup_result.get("supervisor_json_path"):
            print(f"  Summary (JSON): {sup_result.get('supervisor_json_path')}")
        if sup_result.get("overall_summary"):
            print(f"  Overall summary: {sup_result.get('overall_summary')}")
        return 0 if sup_result.get("status") == "ok" else 1

    # Resolve task identifier/path if provided
    task_identifier = args.task_path or args.task_file or args.task_id

    allowed_modes = {"auto", "stages", "task"}
    run_mode = args.mode if args.mode in allowed_modes else "auto"
    task_mode = run_mode == "task" or bool(task_identifier)

    try:
        if task_mode:
            if not task_identifier:
                print("[ERROR] Task mode requested but no --task/--task-id provided.")
                return 1
            resolved_task = task_identifier
            if not os.path.exists(resolved_task):
                legacy_candidate = os.path.join(
                    "meta_agent", "tasks", f"{task_identifier}.md"
                )
                if os.path.exists(legacy_candidate):
                    resolved_task = legacy_candidate
            report = run_task(resolved_task)
            print(f"[INFO] Task {report.task_id} verdict: {report.verdict}")
            if report.summary:
                print(f"[INFO] Summary: {report.summary}")
            if report.errors:
                print(f"[ERROR] {', '.join(report.errors)}")
            print(f"[INFO] Report: {report.artifacts.report_path}")
            return report.exit_code

        agent = MetaAgent()
        success, stages = agent.run_stage_pipeline(
            override_project_id=args.stage_project_id
        )
    except Exception as exc:
        print(f"[ERROR] Meta-Agent failed: {exc}")
        return 1

    if success:
        cleanup_after_successful_run(stages)
    if not success and getattr(agent, "lock_busy", False):
        return 2
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
