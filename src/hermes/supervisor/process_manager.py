"""Process management for SupervisorAgent orchestration."""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from hermes.supervisor.config import PathsConfig, SupervisorConfig
from hermes.supervisor.events import (
    BaseEvent,
    EventLogger,
    EventType,
)
from hermes.supervisor.process_spec import (
    HealthCheckSpec,
    ProcessSpec,
    ProcessStatus,
)


class ProcessState:
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    CRASHED = "CRASHED"
    FAILED = "FAILED"


@dataclass
class ProcessInfo:
    """Metadata about a managed process."""

    pid: int
    start_time: Optional[datetime]
    last_exit_code: Optional[int]
    last_exit_time: Optional[datetime]

    @property
    def uptime_seconds(self) -> Optional[float]:
        if self.start_time and self.last_exit_time is None:
            return (datetime.now(timezone.utc) - self.start_time).total_seconds()
        return None


@dataclass
class _RuntimeProcess:
    spec: ProcessSpec
    process: Optional[subprocess.Popen] = None
    log_handle: Optional[object] = None
    info: Optional[ProcessInfo] = None
    state: str = ProcessState.STOPPED
    retries: int = 0
    next_restart_at: Optional[float] = None
    last_health: Optional[str] = None
    last_health_ts: Optional[datetime] = None
    last_error: Optional[str] = None


class ProcessManager:
    """Manage multiple named processes for SupervisorAgent."""

    def __init__(
        self,
        paths: PathsConfig,
        config: SupervisorConfig,
        state_dir: Path,
        event_logger: Optional[EventLogger] = None,
        logger: Optional[logging.Logger] = None,
        processes: Optional[Dict[str, ProcessSpec]] = None,
        run_id: str = "",
    ) -> None:
        self.paths = paths
        self.config = config
        self.state_dir = state_dir
        self._events = event_logger
        self.logger = logger or logging.getLogger(__name__)
        self._run_id = run_id
        self._state_path = self.paths.runtime_dir / "state" / "process_state.json"
        self._runtime: Dict[str, _RuntimeProcess] = {}
        self._default_name: Optional[str] = None

        if processes:
            for name, spec in processes.items():
                self._runtime[name] = _RuntimeProcess(spec=spec)
            self._default_name = self._select_default_process(list(processes.keys()))
            self._load_state()

    def get_info(self) -> Optional[ProcessInfo]:
        runtime = self._default_runtime()
        return runtime.info if runtime else None

    def get_state(self) -> str:
        runtime = self._default_runtime()
        return runtime.state if runtime else ProcessState.STOPPED

    def get_status_payload(self) -> dict:
        runtime = self._default_runtime()
        if not runtime:
            return {"managed": False, "state": ProcessState.STOPPED}
        pid = (
            runtime.info.pid
            if runtime.info and self._pid_running(runtime.info.pid)
            else None
        )
        last_exit_time = (
            runtime.info.last_exit_time.isoformat()
            if runtime.info and runtime.info.last_exit_time
            else None
        )
        return {
            "managed": True,
            "state": runtime.state,
            "pid": pid,
            "restarts": runtime.retries,
            "last_exit_code": runtime.info.last_exit_code if runtime.info else None,
            "last_exit_time": last_exit_time,
        }

    @property
    def default_name(self) -> Optional[str]:
        return self._default_name

    def status(self, name: str) -> ProcessStatus:
        runtime = self._require_runtime(name)
        pid = runtime.info.pid if runtime.info else None
        is_running = pid is not None and self._pid_running(pid)
        last_start_ts = (
            runtime.info.start_time.isoformat()
            if runtime.info and runtime.info.start_time
            else None
        )
        last_health_ts = (
            runtime.last_health_ts.isoformat() if runtime.last_health_ts else None
        )
        return ProcessStatus(
            name=name,
            pid=pid,
            is_running=is_running,
            state=runtime.state,
            last_start_ts=last_start_ts,
            last_exit_code=runtime.info.last_exit_code if runtime.info else None,
            last_health=runtime.last_health,
            last_health_ts=last_health_ts,
            retries=runtime.retries,
            last_error=runtime.last_error,
        )

    def status_all(self) -> Dict[str, dict]:
        return {name: self.status(name).to_dict() for name in self._runtime}

    def is_running(self) -> bool:
        runtime = self._default_runtime()
        if not runtime or not runtime.info:
            return False
        return self._pid_running(runtime.info.pid)

    def is_running_named(self, name: str) -> bool:
        runtime = self._require_runtime(name)
        return self._is_runtime_running(runtime)

    def start(self, name: str) -> ProcessInfo:
        runtime = self._require_runtime(name)
        if runtime.spec.enabled is False:
            self.logger.warning(
                "Process '%s' is disabled in config; manual start requested.", name
            )
        if self._is_runtime_running(runtime):
            self.logger.info(
                "Process '%s' already running with PID %s", name, runtime.info.pid
            )
            return runtime.info  # type: ignore[return-value]
        runtime.state = ProcessState.STARTING
        runtime.last_error = None
        self._write_state()
        info = self._spawn_process(runtime)
        runtime.info = info
        if self._is_runtime_running(runtime):
            runtime.state = ProcessState.RUNNING
        else:
            runtime.state = ProcessState.CRASHED
            runtime.last_error = "immediate-exit"
        runtime.retries = 0
        runtime.next_restart_at = None
        self._write_state()
        if runtime.state != ProcessState.RUNNING:
            self._log_process_event(
                "PROCESS_EXIT",
                "WARN",
                {"name": name, "pid": info.pid, "reason": "immediate-exit"},
            )
            raise RuntimeError(f"Process '{name}' exited during startup")
        self._log_process_event(
            "PROCESS_START", "INFO", {"name": name, "pid": info.pid}
        )
        if name == self._default_name:
            self._log_bot_event(
                "BOT_START", {"mode": self.config.mode, "pid": info.pid}
            )
        return info

    def stop(self, name: str, graceful_timeout_s: float = 10.0) -> None:
        runtime = self._require_runtime(name)
        if not self._is_runtime_running(runtime):
            runtime.state = ProcessState.STOPPED
            runtime.retries = 0
            runtime.next_restart_at = None
            self._write_state()
            return
        pid = runtime.info.pid if runtime.info else None
        self.logger.info("Stopping process '%s' (PID %s)", name, pid)
        if runtime.process:
            runtime.process.terminate()
            try:
                runtime.process.wait(timeout=graceful_timeout_s)
            except subprocess.TimeoutExpired:
                self.logger.warning(
                    "Process '%s' graceful stop timed out; forcing termination.", name
                )
                self._force_kill(pid)
                try:
                    runtime.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.logger.error(
                        "Failed to confirm process termination for '%s'", name
                    )
            runtime.info.last_exit_code = runtime.process.returncode
            runtime.info.last_exit_time = datetime.now(timezone.utc)
        else:
            self._terminate_external(pid, graceful_timeout_s)
            if runtime.info and runtime.info.last_exit_time is None:
                runtime.info.last_exit_time = datetime.now(timezone.utc)
        self._cleanup_process(runtime)
        runtime.state = ProcessState.STOPPED
        runtime.retries = 0
        runtime.next_restart_at = None
        self._write_state()
        self._log_process_event("PROCESS_STOP", "INFO", {"name": name, "pid": pid})
        if name == self._default_name:
            self._log_bot_event("BOT_STOP", {"reason": "manual", "pid": pid})

    def stop_all(self) -> None:
        for name in list(self._runtime.keys()):
            try:
                self.stop(name)
            except Exception as exc:
                self.logger.warning("Failed to stop process '%s': %s", name, exc)

    def restart(self, name: str) -> ProcessInfo:
        self.stop(name)
        runtime = self._require_runtime(name)
        delay = runtime.spec.restart.backoff_for_attempt(1)
        if delay > 0:
            time.sleep(delay)
        info = self.start(name)
        self._log_process_event(
            "PROCESS_RESTART", "INFO", {"name": name, "pid": info.pid}
        )
        return info

    def ensure_all(self) -> None:
        for name, runtime in self._runtime.items():
            if runtime.spec.enabled:
                try:
                    self.ensure(name)
                except Exception as exc:
                    self.logger.error("Failed to ensure process '%s': %s", name, exc)

    def ensure(self, name: str) -> None:
        runtime = self._require_runtime(name)
        if runtime.spec.enabled and not self._is_runtime_running(runtime):
            self.start(name)

    def tick_restarts(self) -> None:
        now = time.monotonic()
        for name, runtime in self._runtime.items():
            self._refresh_state(name, runtime)
            if runtime.state != ProcessState.CRASHED:
                continue
            policy = runtime.spec.restart
            if not policy.enabled:
                continue
            if runtime.retries >= policy.max_retries:
                runtime.state = ProcessState.FAILED
                self._write_state()
                continue
            if runtime.next_restart_at is None:
                runtime.retries += 1
                delay = max(
                    policy.backoff_for_attempt(runtime.retries), policy.cooldown_s
                )
                runtime.next_restart_at = now + delay
                self._write_state()
                continue
            if now < runtime.next_restart_at:
                continue
            runtime.next_restart_at = None
            self._write_state()
            try:
                info = self.start(name)
                self._log_process_event(
                    "PROCESS_RESTART", "INFO", {"name": name, "pid": info.pid}
                )
            except Exception as exc:
                runtime.last_error = str(exc)
                runtime.state = ProcessState.CRASHED
                self._write_state()

    def tick_healthchecks(self) -> None:
        for name, runtime in self._runtime.items():
            if runtime.spec.healthcheck.type == "none":
                continue
            if not self._is_runtime_running(runtime):
                runtime.last_health = "stopped"
                runtime.last_health_ts = datetime.now(timezone.utc)
                self._write_state()
                continue
            ok = self._run_healthcheck(runtime.spec.healthcheck)
            new_status = "ok" if ok else "fail"
            prev_status = runtime.last_health
            runtime.last_health = new_status
            runtime.last_health_ts = datetime.now(timezone.utc)
            self._write_state()
            if prev_status != new_status:
                event_type = "HEALTH_OK" if ok else "HEALTH_FAIL"
                severity = "INFO" if ok else "WARN"
                self._log_process_event(
                    event_type, severity, {"name": name, "health": runtime.last_health}
                )

    def tick(self) -> None:
        self.logger.debug("ProcessManager tick()")
        self.ensure_all()
        self.tick_restarts()
        self.tick_healthchecks()

    # Internal helpers
    def _select_default_process(self, names: list[str]) -> Optional[str]:
        for name in names:
            if name.lower() == "bot":
                return name
        for name in names:
            if "bot" in name.lower():
                return name
        return names[0] if names else None

    def _default_runtime(self) -> Optional[_RuntimeProcess]:
        if not self._default_name:
            return None
        return self._runtime.get(self._default_name)

    def _require_runtime(self, name: str) -> _RuntimeProcess:
        if name not in self._runtime:
            raise KeyError(f"Unknown process '{name}'")
        return self._runtime[name]

    def _spawn_process(self, runtime: _RuntimeProcess) -> ProcessInfo:
        spec = runtime.spec
        log_handle = self._open_log_file(spec.name)
        cmd = list(spec.cmd)
        if cmd and cmd[0] in ("python", "python3"):
            import sys

            cmd[0] = sys.executable
        env = os.environ.copy()
        env.update(spec.env)

        # Propagate parent environment variables starting with QE_
        for k, v in os.environ.items():
            if k.startswith("QE_") and k not in env:
                env[k] = v

        # Set absolute PYTHONPATH targeting the src/ directory
        src_path = self.paths.qe_root / "src"
        current_pythonpath = env.get("PYTHONPATH", "")
        if current_pythonpath:
            env["PYTHONPATH"] = f"{src_path}{os.pathsep}{current_pythonpath}"
        else:
            env["PYTHONPATH"] = str(src_path)

        env["RUN_ID"] = self._run_id
        creationflags = 0
        if os.name == "nt" and hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
        try:
            process = subprocess.Popen(
                cmd,
                cwd=spec.cwd,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
                env=env,
            )
        except Exception:
            log_handle.close()
            raise
        runtime.process = process
        runtime.log_handle = log_handle
        info = ProcessInfo(
            pid=process.pid,
            start_time=datetime.now(timezone.utc),
            last_exit_code=None,
            last_exit_time=None,
        )
        runtime.info = info
        return info

    def _open_log_file(self, name: str) -> object:
        logs_dir = self.paths.logs_dir / "processes"
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = logs_dir / f"{name}.log"
        self._rotate_log(log_path)
        return log_path.open("a", encoding="utf-8")

    def _rotate_log(
        self, path: Path, max_bytes: int = 10 * 1024 * 1024, backups: int = 3
    ) -> None:
        if not path.exists():
            return
        try:
            if path.stat().st_size < max_bytes:
                return
        except OSError:
            return
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        rotated = path.with_suffix(f".{timestamp}.log")
        try:
            path.replace(rotated)
        except OSError:
            return
        for extra in sorted(
            path.parent.glob(f"{path.stem}.*.log"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        ):
            backups -= 1
            if backups <= 0:
                try:
                    extra.unlink()
                except OSError:
                    pass

    def _refresh_state(self, name: str, runtime: _RuntimeProcess) -> None:
        if runtime.process:
            return_code = runtime.process.poll()
            if return_code is None:
                runtime.state = ProcessState.RUNNING
                return
            runtime.info.last_exit_code = return_code
            runtime.info.last_exit_time = datetime.now(timezone.utc)
            self.logger.warning(
                f"Process {name} exited with code={return_code}, marking CRASHED"
            )
            runtime.state = ProcessState.CRASHED
            runtime.last_error = f"exit_code={return_code}"
            self._cleanup_process(runtime)
            self._write_state()
            self._log_process_event(
                "PROCESS_EXIT", "WARN", {"name": name, "exit_code": return_code}
            )
            if name == self._default_name:
                self._log_bot_event(
                    "BOT_STOP", {"reason": "unexpected-exit", "exit_code": return_code}
                )
            return
        if runtime.info and runtime.info.pid:
            alive = self._pid_running(runtime.info.pid)
            if alive:
                runtime.state = ProcessState.RUNNING
                return
            if runtime.info.last_exit_time is None:
                runtime.info.last_exit_time = datetime.now(timezone.utc)
            runtime.state = ProcessState.CRASHED
            self._write_state()

    def _run_healthcheck(self, spec: HealthCheckSpec) -> bool:
        if spec.type == "http":
            return _http_health(spec.url, spec.timeout_s)
        if spec.type == "tcp":
            return _tcp_health(spec.host, spec.port, spec.timeout_s)
        return True

    def _is_runtime_running(self, runtime: _RuntimeProcess) -> bool:
        if runtime.process and runtime.process.poll() is None:
            return True
        if runtime.info and runtime.info.pid:
            return self._pid_running(runtime.info.pid)
        return False

    def _cleanup_process(self, runtime: _RuntimeProcess) -> None:
        if runtime.log_handle:
            try:
                runtime.log_handle.close()
            except Exception:
                pass
        runtime.log_handle = None
        runtime.process = None

    def _pid_running(self, pid: int) -> bool:
        if pid <= 0:
            return False
        if os.name == "nt":
            try:
                result = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                output = result.stdout or ""
                if "No tasks are running" in output:
                    return False
                for line in output.splitlines():
                    parts = line.split()
                    if len(parts) >= 2 and parts[1].isdigit() and int(parts[1]) == pid:
                        return True
                return False
            except Exception:
                return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        else:
            return True

    def _terminate_external(self, pid: Optional[int], timeout: float) -> None:
        if not pid:
            return
        try:
            os.kill(pid, signal.SIGTERM)
        except PermissionError:
            self.logger.warning("Permission error while sending SIGTERM to PID %s", pid)
        except OSError:
            pass
        end_time = time.time() + timeout
        while time.time() < end_time:
            if not self._pid_running(pid):
                return
            time.sleep(0.5)
        self._force_kill(pid)

    def _force_kill(self, pid: Optional[int]) -> None:
        if not pid:
            return
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True
            )
        else:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

    def _write_state(self) -> None:
        payload = {}
        for name, runtime in self._runtime.items():
            payload[name] = {
                "pid": runtime.info.pid if runtime.info else None,
                "start_ts": (
                    runtime.info.start_time.isoformat()
                    if runtime.info and runtime.info.start_time
                    else None
                ),
                "last_exit_code": runtime.info.last_exit_code if runtime.info else None,
                "last_exit_time": (
                    runtime.info.last_exit_time.isoformat()
                    if runtime.info and runtime.info.last_exit_time
                    else None
                ),
                "retries": runtime.retries,
                "last_health": runtime.last_health,
                "last_health_ts": (
                    runtime.last_health_ts.isoformat()
                    if runtime.last_health_ts
                    else None
                ),
                "last_error": runtime.last_error,
                "state": runtime.state,
            }
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._state_path.with_suffix(".tmp")
            with tmp_path.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
            tmp_path.replace(self._state_path)
        except OSError as exc:
            self.logger.debug("Failed to write process state: %s", exc)

    def _load_state(self) -> None:
        if not self._state_path.exists():
            return
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
        except Exception as exc:
            self.logger.debug("Failed to read process state: %s", exc)
            return
        if not isinstance(raw, dict):
            return
        for name, data in raw.items():
            runtime = self._runtime.get(name)
            if not runtime or not isinstance(data, dict):
                continue
            pid = data.get("pid")
            start_ts = _parse_iso(data.get("start_ts"))
            last_exit_time = _parse_iso(data.get("last_exit_time"))
            runtime.info = (
                ProcessInfo(
                    pid=int(pid) if pid else 0,
                    start_time=start_ts,
                    last_exit_code=data.get("last_exit_code"),
                    last_exit_time=last_exit_time,
                )
                if pid
                else None
            )
            runtime.retries = int(data.get("retries") or 0)
            runtime.last_health = data.get("last_health")
            runtime.last_health_ts = _parse_iso(data.get("last_health_ts"))
            runtime.last_error = data.get("last_error")
            runtime.state = data.get("state", runtime.state)
            if runtime.info and self._pid_running(runtime.info.pid):
                runtime.state = ProcessState.RUNNING
            elif runtime.info:
                runtime.state = ProcessState.STOPPED

    def _log_process_event(self, event_type: str, severity: str, fields: dict) -> None:
        if not self._events:
            return
        name = fields.get("name") if isinstance(fields, dict) else None
        component = self._component_for_process(name)
        try:
            event = BaseEvent(
                ts=datetime.now(timezone.utc),
                type=EventType(event_type),
                source=component,
                data=fields,
                severity=severity,
                run_id=self._run_id,
            )
        except ValueError:
            event = BaseEvent(
                ts=datetime.now(timezone.utc),
                type=EventType.ANOMALY,
                source=component,
                data={"event_type": event_type, **fields},
                severity=severity,
                run_id=self._run_id,
            )
        self._events.log_event(event)

    def _log_bot_event(self, event_type: str, fields: dict) -> None:
        if not self._events:
            return
        component = self._component_for_process(self._default_name)
        try:
            event = BaseEvent(
                ts=datetime.now(timezone.utc),
                type=EventType(event_type),
                source=component,
                data=fields,
                severity="INFO",
                run_id=self._run_id,
            )
        except ValueError:
            event = BaseEvent(
                ts=datetime.now(timezone.utc),
                type=EventType.ANOMALY,
                source=component,
                data={"event_type": event_type, **fields},
                severity="INFO",
                run_id=self._run_id,
            )
        self._events.log_event(event)

    def _component_for_process(self, name: Optional[str]) -> str:
        if not name:
            return "supervisor"
        lowered = str(name).lower()
        if lowered == "hub":
            return "hub"
        if lowered.startswith("bot"):
            return f"bot:{name}"
        return "supervisor"


def _http_health(url: Optional[str], timeout_s: float) -> bool:
    if not url:
        return False
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return int(resp.status) == 200
    except Exception:
        return False


def _tcp_health(host: Optional[str], port: Optional[int], timeout_s: float) -> bool:
    if not host or port is None:
        return False
    try:
        with socket.create_connection((host, int(port)), timeout=timeout_s):
            return True
    except Exception:
        return False


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None
