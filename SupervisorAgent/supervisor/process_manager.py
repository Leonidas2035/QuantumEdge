"""Process management for the QuantumEdge trading engine."""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from supervisor.config import PathsConfig, SupervisorConfig
from supervisor import state as state_utils
from supervisor.events import EventLogger


@dataclass
class ProcessInfo:
    """Metadata about the managed QuantumEdge process."""

    pid: int
    start_time: Optional[datetime]
    last_exit_code: Optional[int]
    last_exit_time: Optional[datetime]

    @property
    def uptime_seconds(self) -> Optional[float]:
        if self.start_time and self.last_exit_time is None:
            return (datetime.now(timezone.utc) - self.start_time).total_seconds()
        return None


class ProcessManager:
    """Starts, stops, and tracks the QuantumEdge child process."""

    def __init__(
        self,
        paths: PathsConfig,
        config: SupervisorConfig,
        state_dir: Path,
        event_logger: Optional[EventLogger] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.paths = paths
        self.config = config
        self.state_dir = state_dir
        self._events = event_logger
        self.logger = logger or logging.getLogger(__name__)

        self._process: Optional[subprocess.Popen] = None
        self._child_log_file = None
        self._info: Optional[ProcessInfo] = state_utils.load_process_info(state_dir)
        self._restart_attempts = 0

        if self._info and not self._pid_running(self._info.pid):
            self.logger.info("Found stale process state; marking process as stopped.")
            if self._info.last_exit_time is None:
                self._info.last_exit_time = datetime.now(timezone.utc)
            state_utils.save_process_info(self.state_dir, self._info)

    def get_info(self) -> Optional[ProcessInfo]:
        return self._info

    def is_running(self) -> bool:
        """Check whether the managed process is currently alive."""

        if self._process:
            return_code = self._process.poll()
            if return_code is None:
                return True
            self.logger.warning("QuantumEdge exited with code %s", return_code)
            self._update_exit_state(return_code)
            if self._events:
                self._events.log_anomaly("unexpected_exit", f"Process exited with code {return_code}", {"pid": self._info.pid if self._info else None})
                self._log_bot_stop("unexpected-exit")
            self._cleanup_process_handles()
            return False

        if self._info:
            alive = self._pid_running(self._info.pid)
            if alive:
                return True
            if self._info.last_exit_time is None:
                self._update_exit_state(self._info.last_exit_code)

        return False

    def start(self, mode: str) -> ProcessInfo:
        """Start the QuantumEdge process if not already running."""

        if mode == "off":
            raise ValueError("Cannot start QuantumEdge when mode is 'off'.")

        if self.is_running():
            self.logger.info("QuantumEdge already running with PID %s", self._info.pid)
            return self._info  # type: ignore[return-value]

        attempts = 0
        last_error: Optional[Exception] = None

        while attempts <= self.config.restart_max_attempts:
            attempts += 1
            try:
                info = self._spawn_process(mode)
                time.sleep(0.5)
                if self._process and self._process.poll() is None:
                    self.logger.info("Started QuantumEdge PID %s (attempt %s)", info.pid, attempts)
                    self._restart_attempts = 0
                    if self._events:
                        self._events.log_bot_start(mode, info)
                    return info
                # Process exited immediately
                return_code = self._process.poll() if self._process else None
                self.logger.error("QuantumEdge exited immediately with code %s", return_code)
                last_error = RuntimeError(f"Immediate exit with code {return_code}")
                self._update_exit_state(return_code)
                self._log_bot_stop("immediate-exit")
                if self._events:
                    self._events.log_anomaly("immediate_exit", f"Process exited during startup with code {return_code}")
            except Exception as exc:
                last_error = exc
                self.logger.exception("Failed to start QuantumEdge (attempt %s/%s)", attempts, self.config.restart_max_attempts + 1)
            self._cleanup_process_handles()
            if attempts <= self.config.restart_max_attempts:
                self.logger.info("Retrying start after %.1fs backoff", self.config.restart_backoff_s)
                time.sleep(self.config.restart_backoff_s)

        raise RuntimeError(f"Unable to start QuantumEdge after {attempts} attempts: {last_error}")

    def stop(self, graceful_timeout_s: float = 10.0) -> None:
        """Stop the managed process."""

        if not self._info or not self.is_running():
            self._cleanup_process_handles()
            return

        pid = self._info.pid
        self.logger.info("Stopping QuantumEdge PID %s", pid)

        if self._process:
            stop_reason = "graceful-stop"
            self._process.terminate()
            try:
                self._process.wait(timeout=graceful_timeout_s)
            except subprocess.TimeoutExpired:
                self.logger.warning("Graceful stop timed out; forcing termination.")
                self._force_kill(pid)
                stop_reason = "forced-kill"
                try:
                    self._process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.logger.error("Failed to confirm process termination for PID %s", pid)
            self._update_exit_state(self._process.returncode)
            self._log_bot_stop(stop_reason)
            self._cleanup_process_handles()
            return

        # No local handle; fall back to signals and OS tools.
        self._terminate_external(pid, graceful_timeout_s)

    def restart(self, mode: str) -> ProcessInfo:
        """Restart the managed process using the configured backoff."""

        self.stop()
        time.sleep(self.config.restart_backoff_s)
        return self.start(mode)

    # Internal helpers
    def _spawn_process(self, mode: str) -> ProcessInfo:
        qe_root = self.paths.qe_root
        run_bot = Path(self.config.bot_entrypoint)
        if not run_bot.is_absolute():
            run_bot = (qe_root / run_bot).resolve()
        if not run_bot.exists():
            raise FileNotFoundError(f"QuantumEdge entrypoint not found: {run_bot}")

        bot_workdir = Path(self.config.bot_workdir) if self.config.bot_workdir else self.paths.quantumedge_root
        if not bot_workdir.is_absolute():
            bot_workdir = (qe_root / bot_workdir).resolve()

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        log_path = self.paths.logs_dir / f"quantumedge_{timestamp}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)

        log_handle = log_path.open("a", encoding="utf-8")
        cmd = [str(self.paths.python_executable), str(run_bot), f"--mode={mode}"]

        creationflags = 0
        if os.name == "nt" and hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

        env = os.environ.copy()
        env.setdefault("QE_ROOT", str(qe_root))
        env.setdefault("QE_CONFIG_DIR", str(qe_root / "config"))
        env.setdefault("QE_RUNTIME_DIR", str(qe_root / "runtime"))
        env.setdefault("QE_LOGS_DIR", str(qe_root / "logs"))
        env.setdefault("QE_DATA_DIR", str(qe_root / "data"))
        env.setdefault("SUPERVISOR_HOST", self.config.api_host)
        env.setdefault("SUPERVISOR_PORT", str(self.config.heartbeat_port))
        env.setdefault("SUPERVISOR_URL", f"http://{self.config.api_host}:{self.config.heartbeat_port}")
        if self.config.bot_config:
            bot_config = Path(self.config.bot_config)
            if not bot_config.is_absolute():
                bot_config = (qe_root / bot_config).resolve()
            env.setdefault("QE_CONFIG_PATH", str(bot_config))
        py_paths = [str(qe_root), str(self.paths.quantumedge_root)]
        existing = env.get("PYTHONPATH")
        if existing:
            py_paths.append(existing)
        env["PYTHONPATH"] = os.pathsep.join(py_paths)
        if self.config.exchange:
            env["EXCHANGE"] = self.config.exchange
        try:
            process = subprocess.Popen(
                cmd,
                cwd=bot_workdir,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
                env=env,
            )
        except Exception:
            log_handle.close()
            raise

        self._process = process
        self._child_log_file = log_handle
        self._info = ProcessInfo(
            pid=process.pid,
            start_time=datetime.now(timezone.utc),
            last_exit_code=None,
            last_exit_time=None,
        )
        state_utils.save_process_info(self.state_dir, self._info)
        return self._info

    def _update_exit_state(self, exit_code: Optional[int]) -> None:
        if not self._info:
            return
        self._info.last_exit_code = exit_code
        self._info.last_exit_time = datetime.now(timezone.utc)
        state_utils.save_process_info(self.state_dir, self._info)

    def _cleanup_process_handles(self) -> None:
        if self._child_log_file:
            try:
                self._child_log_file.close()
            except Exception:
                pass
        self._child_log_file = None
        self._process = None

    def _log_bot_stop(self, reason: str) -> None:
        if self._events and self._info:
            self._events.log_bot_stop(reason, self._info)

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

    def _terminate_external(self, pid: int, timeout: float) -> None:
        stop_reason = "external-stop"
        try:
            os.kill(pid, signal.SIGTERM)
        except PermissionError:
            self.logger.warning("Permission error while sending SIGTERM to PID %s", pid)
        except OSError:
            pass

        end_time = time.time() + timeout
        while time.time() < end_time:
            if not self._pid_running(pid):
                self._update_exit_state(exit_code=None)
                self._log_bot_stop(stop_reason)
                return
            time.sleep(0.5)

        self.logger.warning("Forcing process termination for PID %s", pid)
        self._force_kill(pid)
        stop_reason = "forced-kill"
        self._update_exit_state(exit_code=None)
        self._log_bot_stop(stop_reason)

    def _force_kill(self, pid: int) -> None:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        else:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
