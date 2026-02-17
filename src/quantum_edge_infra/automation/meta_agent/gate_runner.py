import os
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional


@dataclass
class GateStepResult:
    name: str
    exit_code: Optional[int]
    duration_ms: int
    stdout_path: Optional[str]
    stderr_path: Optional[str]
    timed_out: bool
    error: Optional[str]


@dataclass
class GateResults:
    passed: bool
    steps: List[GateStepResult]
    started_at: str
    finished_at: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _safe_step_name(name: str, index: int) -> str:
    safe = []
    for ch in name.strip():
        if ch.isalnum() or ch in {"-", "_"}:
            safe.append(ch)
        elif ch.isspace():
            safe.append("_")
    label = "".join(safe).strip("_")
    return label or f"step_{index}"


def _resolve_cwd(shadow_root: str, step_cwd: Optional[str]) -> str:
    if not step_cwd:
        return shadow_root
    candidate = os.path.abspath(os.path.join(shadow_root, step_cwd))
    shadow_abs = os.path.abspath(shadow_root)
    if os.path.commonpath([candidate, shadow_abs]) != shadow_abs:
        raise ValueError("Gate step cwd escapes shadow root")
    return candidate


def run_gates(
    shadow_root: str, gates, logger, artifacts_dir: Optional[str] = None
) -> GateResults:
    started_at = _now_iso()
    steps: List[GateStepResult] = []
    passed = True

    gates_dir = artifacts_dir or os.path.join(shadow_root, "gates")
    os.makedirs(gates_dir, exist_ok=True)

    for idx, step in enumerate(gates.steps):
        step_start = time.perf_counter()
        safe_name = _safe_step_name(step.name, idx)
        stdout_path = os.path.join(gates_dir, f"{safe_name}.out")
        stderr_path = os.path.join(gates_dir, f"{safe_name}.err")
        exit_code: Optional[int] = None
        timed_out = False
        error: Optional[str] = None

        try:
            cwd = _resolve_cwd(shadow_root, step.cwd)
            env = os.environ.copy()
            if step.env:
                env.update({str(k): str(v) for k, v in step.env.items()})
            with (
                open(stdout_path, "w", encoding="utf-8") as out_handle,
                open(stderr_path, "w", encoding="utf-8") as err_handle,
            ):
                proc = subprocess.run(
                    step.cmd,
                    cwd=cwd,
                    env=env,
                    stdout=out_handle,
                    stderr=err_handle,
                    timeout=step.timeout_seconds,
                    check=False,
                    text=True,
                )
            exit_code = proc.returncode
            if exit_code != 0:
                passed = False
        except subprocess.TimeoutExpired:
            timed_out = True
            passed = False
            error = "timeout"
        except Exception as exc:
            passed = False
            error = str(exc)

        duration_ms = _elapsed_ms(step_start)
        steps.append(
            GateStepResult(
                name=step.name,
                exit_code=exit_code,
                duration_ms=duration_ms,
                stdout_path=stdout_path if os.path.exists(stdout_path) else None,
                stderr_path=stderr_path if os.path.exists(stderr_path) else None,
                timed_out=timed_out,
                error=error,
            )
        )

        logger.info(
            "Gate step completed name=%s exit_code=%s timed_out=%s",
            step.name,
            exit_code,
            timed_out,
        )

        if not passed and not step.continue_on_fail:
            break

    finished_at = _now_iso()
    return GateResults(
        passed=passed,
        steps=steps,
        started_at=started_at,
        finished_at=finished_at,
    )
