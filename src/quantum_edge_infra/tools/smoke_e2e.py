import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional
from urllib import request

import yaml
import shutil


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _run(
    cmd: list[str], env: dict, label: str, ok_codes: Optional[set[int]] = None
) -> None:
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
    ok_codes = ok_codes or {0}
    if proc.returncode not in ok_codes:
        print(f"[FAIL] {label}: code={proc.returncode}")
        print(proc.stdout)
        print(proc.stderr)
        raise RuntimeError(label)
    print(f"[OK] {label}")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _write_task(path: Path) -> None:
    payload = {
        "task_id": "smoke_task",
        "created_at": "2026-01-01T00:00:00Z",
        "project_id": "meta_agent",
        "project_root": ".",
        "objective": "Smoke test task",
        "instructions": "Do not change code; this is a dry run.",
        "execution": {"dry_run": True},
        "constraints": {"patch_only": True, "max_files": 1, "max_file_bytes": 65536},
        "context": {"include_globs": ["docs/operations.md"]},
        "mode": "task",
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _write_schedule(path: Path) -> None:
    payload = {
        "schedule_id": "smoke_schedule",
        "enabled": True,
        "timezone": "Europe/Kyiv",
        "project_id": "meta_agent",
        "inbox_dir": "runtime/inbox",
        "archive_dir": "runtime/inbox_done",
        "failed_dir": "runtime/inbox_failed",
        "windows": [{"days": ["*"], "start": "00:00", "end": "23:59"}],
        "trigger": {"type": "interval", "every_seconds": 300},
        "task_template": {
            "objective": "Smoke dry run",
            "instructions": "Do not change code.",
            "execution": {"dry_run": True},
            "constraints": {
                "patch_only": True,
                "max_files": 1,
                "max_file_bytes": 65536,
            },
            "mode": "task",
        },
        "policy": {"max_concurrent": 1, "max_runs_per_window": 1, "max_attempts": 1},
        "retries": {
            "enabled": True,
            "backoff_base_seconds": 1,
            "backoff_max_seconds": 2,
            "jitter": False,
        },
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _wait_for_health(port: int, token: str, timeout_seconds: int = 8) -> None:
    deadline = time.time() + timeout_seconds
    url = f"http://127.0.0.1:{port}/api/health"
    while time.time() < deadline:
        try:
            req = request.Request(url, headers={"X-CC-Token": token})
            with request.urlopen(req, timeout=2) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            if payload.get("ok") is True:
                return
        except Exception:
            time.sleep(0.3)
    raise RuntimeError("UI health check failed")


def main() -> int:
    repo_root = _repo_root()
    with tempfile.TemporaryDirectory() as tmp_dir:
        runtime_dir = Path(tmp_dir) / "runtime"
        inbox_dir = runtime_dir / "inbox"
        schedules_dir = runtime_dir / "schedules"
        inbox_dir.mkdir(parents=True, exist_ok=True)
        schedules_dir.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        env["QE_ROOT"] = str(repo_root)
        env["META_AGENT_RUNTIME_DIR"] = str(runtime_dir)
        env["META_AGENT_MOCK_LLM_RESPONSE"] = ""

        if shutil.which("meta-agent"):
            _run(["meta-agent", "version"], env, "meta-agent version")

        _run([sys.executable, "meta_agent.py", "diag"], env, "diag")
        _run([sys.executable, "meta_agent.py", "health"], env, "health")

        task_path = Path(tmp_dir) / "smoke_task.yaml"
        _write_task(task_path)
        _run(
            [sys.executable, "meta_agent.py", "run-task", "--task", str(task_path)],
            env,
            "run-task",
            ok_codes={0, 10, 11},
        )

        schedule_path = schedules_dir / "smoke_schedule.yaml"
        _write_schedule(schedule_path)
        _run(
            [
                sys.executable,
                "meta_agent.py",
                "run-scheduler",
                "--once",
                "--schedules-dir",
                str(schedules_dir),
            ],
            env,
            "run-scheduler",
        )

        port = _free_port()
        token = "smoke-token"
        ui_proc = subprocess.Popen(
            [
                sys.executable,
                "meta_agent.py",
                "ui",
                "--port",
                str(port),
                "--bind",
                "127.0.0.1",
                "--token",
                token,
            ],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            _wait_for_health(port, token)
            print("[OK] ui health")
        finally:
            ui_proc.terminate()
            ui_proc.wait(timeout=5)

    print("Smoke E2E: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
