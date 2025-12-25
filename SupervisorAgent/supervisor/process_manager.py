"""Process management for the QuantumEdge trading engine."""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

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


class BotState:
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    CRASHED = "CRASHED"
    FAILED = "FAILED"


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
        self._next_restart_at: Optional[float] = None
        self._state = BotState.STOPPED
        self._stop_requested = False
        self._auto_start_suspended = False
        self._state_path = self.state_dir / "bot.state.json"

        if self._info and not self._pid_running(self._info.pid):
            self.logger.info("Found stale process state; marking process as stopped.")
            if self._info.last_exit_time is None:
                self._info.last_exit_time = datetime.now(timezone.utc)
            state_utils.save_process_info(self.state_dir, self._info)
            self._state = BotState.STOPPED
        elif self._info and self._pid_running(self._info.pid):
            self._state = BotState.RUNNING

        self._write_bot_state()

    def get_info(self) -> Optional[ProcessInfo]:
        return self._info

    def get_state(self) -> str:
        return self._state

    def get_status_payload(self) -> dict:
        pid = self._info.pid if self._info and self._pid_running(self._info.pid) else None
        last_exit_code = self._info.last_exit_code if self._info else None
        last_exit_time = self._info.last_exit_time.isoformat() if self._info and self._info.last_exit_time else None
        return {
            "managed": True,
            "state": self._state,
            "pid": pid,
            "restarts": self._restart_attempts,
            "last_exit_code": last_exit_code,
            "last_exit_time": last_exit_time,
        }

    def _load_env_file(self, env: dict) -> list[str]:
        env_file = self.config.bot_env_file
        if not env_file:
            return []
        path = Path(env_file)
        if not path.is_absolute():
            path = (self.paths.qe_root / path).resolve()
        if not path.exists():
            self.logger.warning("Bot env_file missing: %s", path)
            return []

        loaded: list[str] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.lower().startswith("export "):
                    line = line[7:].strip()
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                if not key:
                    continue
                if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
                    value = value[1:-1]
                env[key] = value
                loaded.append(key)
        except OSError as exc:
            self.logger.warning("Failed to read env_file %s: %s", path, exc)
            return []

        if loaded:
            self.logger.info("Loaded %s keys from env_file: %s", len(loaded), ", ".join(sorted(loaded)))
        return loaded

    def _load_bot_config(self) -> dict:
        bot_config = self.config.bot_config
        if not bot_config:
            return {}
        path = Path(bot_config)
        if not path.is_absolute():
            path = (self.paths.qe_root / path).resolve()
        if not path.exists():
            return {}
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("Failed to parse bot config %s: %s", path, exc)
            return {}
        return data if isinstance(data, dict) else {}

    def _env_truthy(self, value: Optional[str]) -> bool:
        if value is None:
            return False
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    def _require_bingx_keys(self, env: dict) -> Optional[list[str]]:
        bot_cfg = self._load_bot_config()
        exchange = (self.config.exchange or bot_cfg.get("app", {}).get("exchange") or bot_cfg.get("exchange") or "").lower()
        if exchange != "bingx_swap":
            return None
        demo_cfg = bot_cfg.get("bingx_demo", {}) or {}
        allow_trading = bool(demo_cfg.get("allow_trading_demo", False))
        allow_place_test = bool(demo_cfg.get("allow_place_test_order", False)) or self._env_truthy(env.get("QE_DEMO_PLACE_TEST_ORDER"))
        if not (allow_trading or allow_place_test):
            return None
        required = ["BINGX_DEMO_API_KEY", "BINGX_DEMO_API_SECRET"]
        missing = [key for key in required if not env.get(key)]
        return missing or None

    def tick(self, mode: str) -> None:
        """Poll process status and apply restart policy."""

        self._refresh_state()
        if self._state == BotState.RUNNING:
            return
        if mode == "off":
            return

        if self._state == BotState.CRASHED and self._next_restart_at is not None:
            if time.monotonic() < self._next_restart_at:
                return
            self._next_restart_at = None

        if self._state in {BotState.STOPPED, BotState.CRASHED}:
            if self._state == BotState.STOPPED and (not self.config.bot_auto_start or self._auto_start_suspended):
                return
            if self._state == BotState.CRASHED and not self.config.bot_restart_enabled:
                return
            try:
                self.start(mode)
            except Exception as exc:
                self.logger.error("Bot start attempt failed: %s", exc)

    def is_running(self) -> bool:
        """Check whether the managed process is currently alive."""
        self._refresh_state()
        return self._state == BotState.RUNNING

    def start(self, mode: str) -> ProcessInfo:
        """Start the QuantumEdge process if not already running."""

        if mode == "off":
            raise ValueError("Cannot start QuantumEdge when mode is 'off'.")

        if self.is_running():
            self.logger.info("Bot already running with PID %s", self._info.pid)
            return self._info  # type: ignore[return-value]

        self._stop_requested = False
        self._auto_start_suspended = False
        self._set_state(BotState.STARTING)
        env = os.environ.copy()
        self._load_env_file(env)
        missing = self._require_bingx_keys(env)
        if missing:
            self.logger.error("Missing required BingX demo keys: %s", ", ".join(missing))
            self._set_state(BotState.FAILED)
            self._auto_start_suspended = True
            raise RuntimeError("Missing BingX demo credentials.")

        info = self._spawn_process(mode, env)
        time.sleep(0.5)
        if self._process and self._process.poll() is None:
            self._set_state(BotState.RUNNING)
            self._restart_attempts = 0
            self._next_restart_at = None
            self._write_bot_state()
            if self._events:
                self._events.log_bot_start(mode, info)
            self.logger.info("Bot started with PID %s", info.pid)
            return info
        return_code = self._process.poll() if self._process else None
        self.logger.error("Bot exited immediately with code %s", return_code)
        self._handle_exit(return_code, "immediate-exit")
        raise RuntimeError(f"Bot exited during startup with code {return_code}")

    def stop(self, graceful_timeout_s: float = 10.0) -> None:
        """Stop the managed process."""
        self._stop_requested = True
        self._auto_start_suspended = True

        if not self._info or not self.is_running():
            self._cleanup_process_handles()
            self._set_state(BotState.STOPPED)
            self._restart_attempts = 0
            self._next_restart_at = None
            self._write_bot_state()
            return

        pid = self._info.pid
        self.logger.info("Stopping bot PID %s", pid)

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
            self._set_state(BotState.STOPPED)
            self._restart_attempts = 0
            self._next_restart_at = None
            self._write_bot_state()
            return

        # No local handle; fall back to signals and OS tools.
        self._terminate_external(pid, graceful_timeout_s)
        self._set_state(BotState.STOPPED)
        self._restart_attempts = 0
        self._next_restart_at = None
        self._write_bot_state()

    def restart(self, mode: str) -> ProcessInfo:
        """Restart the managed process using the configured backoff."""
        self._restart_attempts = 0
        self._next_restart_at = None
        self.stop()
        time.sleep(self._restart_delay())
        return self.start(mode)

    # Internal helpers
    def _spawn_process(self, mode: str, env: dict) -> ProcessInfo:
        qe_root = self.paths.qe_root
        run_bot = Path(self.config.bot_entrypoint)
        if not run_bot.is_absolute():
            run_bot = (qe_root / run_bot).resolve()
        if not run_bot.exists():
            raise FileNotFoundError(f"QuantumEdge entrypoint not found: {run_bot}")

        bot_workdir = Path(self.config.bot_workdir) if self.config.bot_workdir else self.paths.quantumedge_root
        if not bot_workdir.is_absolute():
            bot_workdir = (qe_root / bot_workdir).resolve()

        log_path = self.paths.logs_dir / "bot.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)

        log_handle = log_path.open("a", encoding="utf-8")
        cmd = [str(self.paths.python_executable), str(run_bot), f"--mode={mode}"]

        creationflags = 0
        if os.name == "nt" and hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

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
            env.setdefault("BOT_CONFIG_PATH", str(bot_config))
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

    def _set_state(self, state: str) -> None:
        if state != self._state:
            self._state = state
            self._write_bot_state()

    def _write_bot_state(self) -> None:
        payload = {
            "state": self._state,
            "pid": self._info.pid if self._info else None,
            "restarts": self._restart_attempts,
            "last_exit_code": self._info.last_exit_code if self._info else None,
            "last_exit_time": self._info.last_exit_time.isoformat() if self._info and self._info.last_exit_time else None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            with self._state_path.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
        except OSError as exc:
            self.logger.debug("Failed to write bot state: %s", exc)

    def _restart_delay(self) -> float:
        backoffs = self.config.bot_restart_backoff_seconds or [self.config.restart_backoff_s]
        index = max(0, min(self._restart_attempts - 1, len(backoffs) - 1))
        try:
            delay = float(backoffs[index])
        except (TypeError, ValueError):
            delay = float(self.config.restart_backoff_s)
        return max(0.0, delay)

    def _schedule_restart(self) -> None:
        if not self.config.bot_restart_enabled:
            return
        self._restart_attempts += 1
        if self._restart_attempts > self.config.bot_restart_max_retries:
            self._set_state(BotState.FAILED)
            self.logger.error("Bot restart attempts exhausted; marking FAILED.")
            return
        delay = self._restart_delay()
        self._next_restart_at = time.monotonic() + delay
        self.logger.warning(
            "Scheduling bot restart in %.1fs (attempt %s/%s)",
            delay,
            self._restart_attempts,
            self.config.bot_restart_max_retries,
        )
        self._write_bot_state()

    def _handle_exit(self, exit_code: Optional[int], reason: str) -> None:
        self._update_exit_state(exit_code)
        self._log_bot_stop(reason)
        if self._events:
            self._events.log_anomaly("bot_exit", f"Bot exited ({reason}) with code {exit_code}", {"pid": self._info.pid if self._info else None})
        self._cleanup_process_handles()
        if self._stop_requested:
            self._set_state(BotState.STOPPED)
            return
        self._set_state(BotState.CRASHED)
        if self.config.bot_restart_enabled:
            self._schedule_restart()

    def _refresh_state(self) -> None:
        if self._process:
            return_code = self._process.poll()
            if return_code is None:
                self._set_state(BotState.RUNNING)
                return
            self.logger.warning("Bot exited with code %s", return_code)
            self._handle_exit(return_code, "unexpected-exit")
            return

        if self._info:
            alive = self._pid_running(self._info.pid)
            if alive:
                self._set_state(BotState.RUNNING)
                return
            if self._info.last_exit_time is None:
                self._update_exit_state(self._info.last_exit_code)
            if self._state == BotState.RUNNING and not self._stop_requested:
                self._set_state(BotState.CRASHED)
                if self.config.bot_restart_enabled:
                    self._schedule_restart()
        elif self._state != BotState.STOPPED:
            self._set_state(BotState.STOPPED)

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
