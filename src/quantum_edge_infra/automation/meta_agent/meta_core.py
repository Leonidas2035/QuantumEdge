import json
import os
import subprocess
import time
from datetime import datetime, timezone
from hashlib import sha256
from typing import Dict, List, Optional

from .llm_client import LLMClient
from .file_manager import ChangeSet, FileChange, build_change_set_from_response
from .gate_runner import GateResults, run_gates
from .logger import configure_logger
from .project_scanner import ProjectScanner
from .prompt_builder import PromptBuilder
from .projects_config import load_project_registry, resolve_project_root
from .run_lock import RunLock, describe_existing_lock, resolve_lock_path
from .safety_policy import SAFETY_POLICY_PATH, evaluate_change_set, load_safety_policy
from .shadow_workspace import cleanup_shadow, create_shadow
from .task_contract import (
    Report,
    ReportArtifacts,
    ReportChanges,
    ReportSafety,
    ReportCLI,
    ReportDurations,
    ReportGateStep,
    ReportGates,
    ReportShadow,
    PatchInfo,
    TaskSpec,
    TaskValidationError,
    load_task_spec,
)
from .write_engine import apply_change_set_with_policy

import yaml

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
CONFIG_PATH = os.path.join("src", "quantum_edge_core", "config", "meta_agent.yaml")
TASKS_DIR = os.path.join(BASE_DIR, "tasks")

try:
    from quantum_edge_infra.tools.qe_config import get_qe_paths
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
    parent = os.path.abspath(os.path.join(BASE_DIR, os.pardir))
    if os.path.isdir(os.path.join(parent, "config")) and os.path.isdir(
        os.path.join(parent, "ai_scalper_bot")
    ):
        return parent
    return BASE_DIR


def _resolve_meta_config_path(path: str = CONFIG_PATH) -> str:
    base = _resolve_base_dir()
    env_override = os.getenv("META_AGENT_CONFIG")
    candidate = env_override or path
    if os.path.isabs(candidate):
        return candidate
    return os.path.abspath(os.path.join(base, candidate))


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _load_config(path: str = CONFIG_PATH) -> Dict:
    resolved = _resolve_meta_config_path(path)
    if not os.path.exists(resolved):
        return {}
    try:
        with open(resolved, "r", encoding="utf-8") as handle:
            if resolved.lower().endswith((".yaml", ".yml")):
                return yaml.safe_load(handle) or {}
            return json.load(handle)
    except (json.JSONDecodeError, yaml.YAMLError, OSError):
        return {}


def _resolve_runtime_dir() -> str:
    base = _resolve_base_dir()
    base_abs = os.path.abspath(base)
    env_meta_runtime = os.getenv("META_AGENT_RUNTIME_DIR")
    if env_meta_runtime:
        candidate = os.path.abspath(env_meta_runtime)
        try:
            if os.path.commonpath([candidate, base_abs]) == base_abs:
                return candidate
        except ValueError:
            pass
    env_runtime = os.getenv("QE_RUNTIME_DIR")
    if env_runtime:
        candidate = os.path.abspath(env_runtime)
        try:
            if os.path.commonpath([candidate, base_abs]) == base_abs:
                return candidate
        except ValueError:
            pass
    return os.path.abspath(os.path.join(base_abs, "runtime"))


def _make_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    short = os.urandom(3).hex()
    return f"{stamp}_{short}"


def _relpath_under_base(path: str, base_abs: str) -> str:
    rel = os.path.relpath(path, base_abs)
    if rel.startswith(".."):
        raise TaskValidationError("Path escapes repo root")
    return rel.replace("\\", "/")


def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _check_timeout(timeout_seconds: Optional[int], start: float) -> None:
    if timeout_seconds is None:
        return
    if time.perf_counter() - start > timeout_seconds:
        raise TimeoutError("Task timed out")


def _build_cli_context(cli_context: Optional[dict]) -> Optional[ReportCLI]:
    if not cli_context:
        return None
    command = str(cli_context.get("command") or "")
    args = cli_context.get("args_sanitized") or []
    return ReportCLI(command=command, args_sanitized=list(args))


def _hash_content(content: str) -> Optional[str]:
    if content is None:
        return None
    return sha256(content.encode("utf-8")).hexdigest()


def _build_summary(verdict: str, applied: bool, files_changed: int) -> str:
    if verdict == "allow":
        if files_changed == 0:
            return "No file changes detected."
        return (
            f"Applied changes to {files_changed} files."
            if applied
            else f"Patches generated for {files_changed} files."
        )
    if verdict == "warn":
        return "Safety warnings: patches generated only."
    if verdict == "block":
        return "Changes blocked by safety policy; patches generated only."
    return "Task failed; see errors for details."


def _log_event(path: str, event: str, payload: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **payload,
        }
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(data, ensure_ascii=True) + "\n")
    except Exception:
        return


def _exit_code_for(verdict: str, error_type: Optional[str] = None) -> int:
    if error_type == "invalid_task":
        return 40
    if error_type == "lock_busy":
        return 50
    if error_type == "dry_run":
        return 11
    if error_type == "gate_failed":
        return 12
    if verdict == "allow":
        return 0
    if verdict == "warn":
        return 10
    if verdict == "block":
        return 20
    return 30


def _resolve_target_project(spec: TaskSpec) -> str:
    if spec.project_root:
        root = spec.project_root
        if not os.path.isabs(root):
            root = os.path.join(_resolve_base_dir(), root)
        return os.path.abspath(root)

    registry = load_project_registry()
    info = resolve_project_root(spec.project_id, registry)
    return str(info.root_path)


def run_basic_quality_checks(
    project_root: str, affected_files: List[str]
) -> Dict[str, any]:
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


def _clone_change_set(change_set: ChangeSet, new_root: str) -> ChangeSet:
    cloned = ChangeSet(project_root=os.path.abspath(new_root), changes={})
    for rel_path, change in change_set.changes.items():
        cloned.changes[rel_path] = FileChange(
            path=rel_path,
            old_content=change.old_content,
            new_content=change.new_content,
        )
    return cloned


def _report_gates_from_results(
    results: GateResults,
    base_abs: str,
    artifacts_dir: Optional[str],
) -> ReportGates:
    steps: List[ReportGateStep] = []
    for step in results.steps:
        stdout_rel = (
            _relpath_under_base(step.stdout_path, base_abs)
            if step.stdout_path
            else None
        )
        stderr_rel = (
            _relpath_under_base(step.stderr_path, base_abs)
            if step.stderr_path
            else None
        )
        steps.append(
            ReportGateStep(
                name=step.name,
                exit_code=step.exit_code,
                duration_ms=step.duration_ms,
                stdout_path=stdout_rel,
                stderr_path=stderr_rel,
                timed_out=step.timed_out,
                error=step.error,
            )
        )

    artifacts_rel = (
        _relpath_under_base(artifacts_dir, base_abs) if artifacts_dir else None
    )
    return ReportGates(
        enabled=True,
        passed=results.passed,
        steps=steps,
        artifacts_dir=artifacts_rel,
        started_at=results.started_at,
        finished_at=results.finished_at,
    )


def _read_shadow_info(run_dir: str) -> Dict[str, str]:
    path = os.path.join(run_dir, "shadow", "shadow_info.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except Exception:
        return {}
    return {}


def run_task(
    task_path: str,
    use_lock: bool = True,
    llm_client: Optional[LLMClient] = None,
    timeout_seconds: Optional[int] = None,
    llm_timeout_seconds: Optional[int] = None,
    cli_context: Optional[dict] = None,
) -> Report:
    """
    Executes a single task spec with safety gating and writes run artifacts.
    """
    started_at = datetime.now(timezone.utc).isoformat()
    overall_start = time.perf_counter()
    finished_at = started_at
    errors: List[str] = []
    lock: RunLock | None = None
    shadow_dir: Optional[str] = None
    shadow_used = False
    shadow_strategy: Optional[str] = None
    shadow_keep = False
    shadow_project_root: Optional[str] = None
    gate_results: Optional[GateResults] = None

    base_abs = os.path.abspath(_resolve_base_dir())
    runtime_dir = _resolve_runtime_dir()
    run_id = _make_run_id()
    log_level = (os.getenv("META_AGENT_LOG_LEVEL") or "INFO").upper()
    logger = configure_logger(
        "meta_agent.run_task", runtime_dir, log_level, run_id=run_id
    )
    run_dir = os.path.join(runtime_dir, "runs", run_id)
    patches_dir = os.path.join(run_dir, "patches")
    gates_dir = os.path.join(run_dir, "gates")
    context_manifest_path = os.path.join(run_dir, "context_manifest.json")
    report_path = os.path.join(run_dir, "report.json")
    task_copy_path = os.path.join(run_dir, "task.yaml")
    changeset_path = os.path.join(run_dir, "changeset.json")
    events_path = os.path.join(run_dir, "events.jsonl")

    rel_report_path = _relpath_under_base(report_path, base_abs)
    rel_patches_dir = _relpath_under_base(patches_dir, base_abs)
    rel_context_manifest_path = _relpath_under_base(context_manifest_path, base_abs)
    rel_task_copy_path = _relpath_under_base(task_copy_path, base_abs)

    if use_lock:
        lock_path = resolve_lock_path(_resolve_base_dir())
        lock = RunLock(lock_path)
        if not lock.acquire():
            detail = describe_existing_lock(lock_path)
            message = "Meta-Agent is already running (lock held)."
            if detail:
                message = f"{message} {detail}"
            verdict = "error"
            exit_code = _exit_code_for(verdict, "lock_busy")
            report = Report(
                run_id=run_id,
                task_id="unknown",
                started_at=started_at,
                finished_at=finished_at,
                verdict=verdict,
                exit_code=exit_code,
                summary=message,
                changes=ReportChanges(),
                safety=ReportSafety(policy_version=None, checks=[]),
                artifacts=ReportArtifacts(
                    report_path=rel_report_path,
                    patches_dir=rel_patches_dir,
                    logs_path=None,
                    context_manifest_path=rel_context_manifest_path,
                    task_path=rel_task_copy_path,
                ),
                errors=[message],
                cli=_build_cli_context(cli_context),
                runtime_dir=_relpath_under_base(runtime_dir, base_abs),
                durations=None,
            )
            os.makedirs(run_dir, exist_ok=True)
            with open(report_path, "w", encoding="utf-8") as handle:
                json.dump(report.to_dict(), handle, indent=2)
            return report

    try:
        _log_event(events_path, "run_started", {"run_id": run_id})
        os.makedirs(patches_dir, exist_ok=True)

        config = _load_config()

        if os.path.exists(task_path):
            spec = load_task_spec(task_path)
        else:
            # Treat task_path as a natural language instruction
            spec = TaskSpec(
                task_id=_make_run_id(),
                created_at=datetime.now(timezone.utc).isoformat(),
                project_id="monorepo",
                project_root=_resolve_base_dir(),
                objective="CLI Request",
                instructions=task_path,
                mode="task",
            )
            spec.validate()

        _log_event(
            events_path,
            "task_loaded",
            {"task_id": spec.task_id, "project_id": spec.project_id},
        )
        _check_timeout(timeout_seconds, overall_start)
        target_project = _resolve_target_project(spec)
        target_project = os.path.abspath(target_project)
        if os.path.commonpath([target_project, base_abs]) != base_abs:
            raise TaskValidationError("project_root must stay within repo root")
        if not os.path.isdir(target_project):
            raise TaskValidationError(f"project_root does not exist: {target_project}")

        spec.project_root = target_project
        shadow_keep = bool(spec.execution.shadow_keep)
        shadow_project_root = target_project
        with open(task_copy_path, "w", encoding="utf-8") as handle:
            yaml.safe_dump(spec.to_dict(), handle, allow_unicode=True, sort_keys=False)

        scan_start = time.perf_counter()
        scanner = ProjectScanner(target_project)

        # Architect Mode: Gather Tree + Source
        tree_view = scanner.get_project_structure()
        source_context = scanner.read_all_code()
        context = f"PROJECT STRUCTURE:\n{tree_view}\n\nSOURCE CODE:\n{source_context}"
        scan_ms = _elapsed_ms(scan_start)
        context_manifest = {
            "project_root": target_project,
            "files_included": scanner.stats.files_included,
            "chars_collected": scanner.stats.chars_collected,
            "stopped_due_to_limit": scanner.stats.stopped_due_to_limit,
            "skipped_large_files": scanner.stats.skipped_large_files,
            "included_files": scanner.stats.included_files,
        }
        with open(context_manifest_path, "w", encoding="utf-8") as handle:
            json.dump(context_manifest, handle, indent=2)
        _log_event(
            events_path,
            "scan_done",
            {
                "files_included": scanner.stats.files_included,
                "chars_collected": scanner.stats.chars_collected,
                "stopped_due_to_limit": scanner.stats.stopped_due_to_limit,
            },
        )

        _check_timeout(timeout_seconds, overall_start)

        instructions = (
            f"Objective: {spec.objective}\n\nInstructions:\n{spec.instructions}"
        )
        prompt_metadata = {
            "task_id": spec.task_id,
            "project_id": spec.project_id,
            "project_root": target_project,
            "run_id": run_id,
            "mode": "task",
        }
        full_prompt = PromptBuilder().build_prompt(
            instructions, context, prompt_metadata
        )

        llm_start = time.perf_counter()
        client = llm_client or LLMClient(
            provider=config.get("provider"),
            model=spec.llm.model,
            temperature=spec.llm.temperature,
            request_timeout_seconds=llm_timeout_seconds,
        )
        system_prompt = (
            "You are a Global Architect. You have full vision of the project structure and source code.\n"
            f"PROJECT STRUCTURE:\n{tree_view}\n\n"
            "Use this context to fulfill the user's request precisely. "
            "Output only file blocks using the format ===FILE: path === followed by the new content."
        )
        response = client.send(full_prompt, system_prompt=system_prompt)
        llm_ms = _elapsed_ms(llm_start)
        _log_event(
            events_path,
            "llm_called",
            {"duration_ms": llm_ms, "model": spec.llm.model or "default"},
        )
        if isinstance(response, str) and response.lstrip().startswith("[ERROR]"):
            raise RuntimeError(response)

        change_set = build_change_set_from_response(target_project, response)
        _log_event(
            events_path, "changeset_built", {"files_changed": len(change_set.changes)}
        )
        try:
            os.makedirs(run_dir, exist_ok=True)
            payload = {
                "project_root": change_set.project_root,
                "changes": {
                    rel: {
                        "old_content": change.old_content,
                        "new_content": change.new_content,
                    }
                    for rel, change in change_set.changes.items()
                },
            }
            with open(changeset_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=True, indent=2)
        except Exception as exc:
            errors.append(f"Failed to write changeset: {exc}")
        _check_timeout(timeout_seconds, overall_start)

        constraint_checks: List[str] = []
        override_verdict: Optional[str] = None
        force_patch_only = False
        if len(change_set.changes) > spec.constraints.max_files:
            override_verdict = "block"
            constraint_checks.append("constraint:max_files_exceeded")
            errors.append("Change set exceeds constraints.max_files")

        oversized = [
            rel
            for rel, change in change_set.changes.items()
            if len(change.new_content.encode("utf-8")) > spec.constraints.max_file_bytes
        ]
        if oversized:
            override_verdict = "block"
            constraint_checks.append("constraint:max_file_bytes_exceeded")
            errors.append(
                f"Files exceed constraints.max_file_bytes: {', '.join(oversized)}"
            )

        if spec.constraints.patch_only and override_verdict != "block":
            override_verdict = "warn"
            constraint_checks.append("constraint:patch_only")
            force_patch_only = True

        safety_start = time.perf_counter()
        policy = load_safety_policy()
        safety_eval = evaluate_change_set(policy, change_set)
        safety_ms = _elapsed_ms(safety_start)
        _log_event(
            events_path,
            "safety_verdict",
            {
                "verdict": safety_eval.overall_verdict,
                "write_mode": safety_eval.write_mode,
            },
        )

        apply_ms = 0
        patch_start = time.perf_counter()
        patch_outcome = apply_change_set_with_policy(
            change_set,
            patches_dir,
            policy=policy,
            precomputed_eval=safety_eval,
            override_verdict=override_verdict,
            force_patch_only=True,
            force_direct=False,
            always_write_patches=True,
        )
        _check_timeout(timeout_seconds, overall_start)
        apply_ms += _elapsed_ms(patch_start)

        verdict = patch_outcome.safety_eval.overall_verdict
        files_changed = len(change_set.changes)
        applied = False
        gate_failed = False
        dry_run_used = False
        gates_enabled = bool(spec.gates.enabled and spec.gates.steps)

        safety_mode = (config.get("safety") or {}).get("mode", "manual")
        is_auto_safety = safety_mode == "auto"

        if (verdict == "allow" or is_auto_safety) and not force_patch_only:
            dry_run_used = bool(spec.execution.dry_run)
            if gates_enabled:
                try:
                    shadow_dir = create_shadow(
                        target_project,
                        run_dir,
                        spec.execution.shadow_strategy,
                        logger,
                        ignore_globs=spec.constraints.deny_globs,
                    )
                    shadow_used = True
                    shadow_info = _read_shadow_info(run_dir)
                    shadow_strategy = (
                        shadow_info.get("strategy") or spec.execution.shadow_strategy
                    )
                    shadow_change_set = _clone_change_set(change_set, shadow_dir)
                    shadow_apply_start = time.perf_counter()
                    apply_change_set_with_policy(
                        shadow_change_set,
                        patches_dir,
                        policy=policy,
                        precomputed_eval=patch_outcome.safety_eval,
                        force_patch_only=False,
                        force_direct=True,
                        always_write_patches=False,
                    )
                    apply_ms += _elapsed_ms(shadow_apply_start)
                    gate_results = run_gates(
                        shadow_dir, spec.gates, logger, artifacts_dir=gates_dir
                    )
                    _log_event(
                        events_path,
                        "gates_result",
                        {
                            "passed": gate_results.passed,
                            "steps": len(gate_results.steps),
                        },
                    )
                    _check_timeout(timeout_seconds, overall_start)
                    if not gate_results.passed:
                        gate_failed = True
                except Exception as exc:
                    gate_failed = True
                    errors.append(f"Gate execution failed: {exc}")

            if not dry_run_used and not gate_failed:
                apply_start = time.perf_counter()
                outcome = apply_change_set_with_policy(
                    change_set,
                    patches_dir,
                    policy=policy,
                    precomputed_eval=patch_outcome.safety_eval,
                    force_patch_only=False,
                    force_direct=True,
                    always_write_patches=False,
                )
                apply_ms += _elapsed_ms(apply_start)
                applied = outcome.applied
                _log_event(
                    events_path,
                    "apply_done",
                    {"applied": applied},
                )

                if applied:
                    for f in outcome.created_files:
                        print(f"[INFO] Successfully wrote to {f}")
                    for f in outcome.changed_files:
                        print(f"[INFO] Successfully wrote to {f}")

                _check_timeout(timeout_seconds, overall_start)

        safety_checks = list(patch_outcome.safety_eval.reasons or [])
        for file_status in patch_outcome.safety_eval.files:
            if file_status.reasons:
                reasons = ", ".join(file_status.reasons)
                safety_checks.append(
                    f"{file_status.path}: {reasons} (verdict={file_status.verdict})"
                )
        safety_checks.extend(constraint_checks)

        patch_info: List[PatchInfo] = []
        for rel_path, change in change_set.changes.items():
            patch_path = os.path.join(patches_dir, f"{rel_path}.patch")
            patch_info.append(
                PatchInfo(
                    path=rel_path,
                    patch_file=_relpath_under_base(patch_path, base_abs),
                    sha_before=_hash_content(change.old_content),
                    sha_after=_hash_content(change.new_content),
                )
            )

        finished_at = datetime.now(timezone.utc).isoformat()
        total_ms = _elapsed_ms(overall_start)
        final_verdict = verdict
        exit_code = _exit_code_for(final_verdict)
        summary = _build_summary(final_verdict, applied, files_changed)

        if gate_failed:
            final_verdict = "warn"
            exit_code = _exit_code_for(final_verdict, "gate_failed")
            summary = "Quality gates failed; patches generated only."
        elif dry_run_used:
            final_verdict = "warn"
            exit_code = _exit_code_for(final_verdict, "dry_run")
            summary = "Dry run completed; patches generated only."

        if gates_enabled:
            if gate_results:
                gates_report = _report_gates_from_results(
                    gate_results, base_abs, gates_dir
                )
            else:
                artifacts_rel = (
                    _relpath_under_base(gates_dir, base_abs)
                    if os.path.isdir(gates_dir)
                    else None
                )
                gates_report = ReportGates(
                    enabled=True,
                    passed=False,
                    steps=[],
                    artifacts_dir=artifacts_rel,
                    started_at=started_at,
                    finished_at=finished_at,
                )
        else:
            gates_report = ReportGates(
                enabled=False,
                passed=True,
                steps=[],
                artifacts_dir=None,
                started_at=started_at,
                finished_at=started_at,
            )

        shadow_dir_rel = (
            _relpath_under_base(shadow_dir, base_abs)
            if shadow_used and shadow_dir
            else None
        )
        shadow_report = ReportShadow(
            used=shadow_used,
            strategy=shadow_strategy,
            kept=shadow_keep if shadow_used else False,
            shadow_dir_rel=shadow_dir_rel,
        )

        report = Report(
            run_id=run_id,
            task_id=spec.task_id,
            started_at=started_at,
            finished_at=finished_at,
            verdict=final_verdict,
            exit_code=exit_code,
            summary=summary,
            changes=ReportChanges(
                patches=patch_info,
                applied=applied,
                files_changed=files_changed,
            ),
            safety=ReportSafety(
                policy_version=os.path.basename(SAFETY_POLICY_PATH),
                checks=safety_checks,
            ),
            artifacts=ReportArtifacts(
                report_path=rel_report_path,
                patches_dir=rel_patches_dir,
                logs_path=None,
                context_manifest_path=rel_context_manifest_path,
                task_path=rel_task_copy_path,
            ),
            errors=errors,
            cli=_build_cli_context(cli_context),
            runtime_dir=_relpath_under_base(runtime_dir, base_abs),
            durations=ReportDurations(
                total_ms=total_ms,
                scan_ms=scan_ms,
                llm_ms=llm_ms,
                safety_ms=safety_ms,
                apply_ms=apply_ms,
            ),
            gates=gates_report,
            shadow=shadow_report,
        )
        _log_event(
            events_path,
            "run_finished",
            {"exit_code": exit_code, "verdict": final_verdict, "applied": applied},
        )
    except TaskValidationError as exc:
        finished_at = datetime.now(timezone.utc).isoformat()
        verdict = "error"
        exit_code = _exit_code_for(verdict, "invalid_task")
        errors.append(str(exc))
        total_ms = _elapsed_ms(overall_start)
        report = Report(
            run_id=run_id,
            task_id="unknown",
            started_at=started_at,
            finished_at=finished_at,
            verdict=verdict,
            exit_code=exit_code,
            summary=str(exc),
            changes=ReportChanges(),
            safety=ReportSafety(policy_version=None, checks=[]),
            artifacts=ReportArtifacts(
                report_path=rel_report_path,
                patches_dir=rel_patches_dir,
                logs_path=None,
                context_manifest_path=rel_context_manifest_path,
                task_path=rel_task_copy_path,
            ),
            errors=errors,
            cli=_build_cli_context(cli_context),
            runtime_dir=_relpath_under_base(runtime_dir, base_abs),
            durations=ReportDurations(
                total_ms=total_ms,
                scan_ms=0,
                llm_ms=0,
                safety_ms=0,
                apply_ms=0,
            ),
        )
        _log_event(
            events_path, "run_finished", {"exit_code": exit_code, "verdict": verdict}
        )
    except Exception as exc:
        finished_at = datetime.now(timezone.utc).isoformat()
        verdict = "error"
        exit_code = _exit_code_for(verdict, "error")
        errors.append(str(exc))
        total_ms = _elapsed_ms(overall_start)
        report = Report(
            run_id=run_id,
            task_id="unknown",
            started_at=started_at,
            finished_at=finished_at,
            verdict=verdict,
            exit_code=exit_code,
            summary=str(exc),
            changes=ReportChanges(),
            safety=ReportSafety(policy_version=None, checks=[]),
            artifacts=ReportArtifacts(
                report_path=rel_report_path,
                patches_dir=rel_patches_dir,
                logs_path=None,
                context_manifest_path=rel_context_manifest_path,
                task_path=rel_task_copy_path,
            ),
            errors=errors,
            cli=_build_cli_context(cli_context),
            runtime_dir=_relpath_under_base(runtime_dir, base_abs),
            durations=ReportDurations(
                total_ms=total_ms,
                scan_ms=0,
                llm_ms=0,
                safety_ms=0,
                apply_ms=0,
            ),
        )
        _log_event(
            events_path, "run_finished", {"exit_code": exit_code, "verdict": verdict}
        )
    finally:
        if shadow_dir:
            cleanup_shadow(
                shadow_dir,
                keep=shadow_keep,
                strategy=shadow_strategy,
                project_root=shadow_project_root,
                logger=logger,
            )
        if lock:
            lock.release()

    os.makedirs(run_dir, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as handle:
        json.dump(report.to_dict(), handle, indent=2)
    return report
