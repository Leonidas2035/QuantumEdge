"""Meta-Agent orchestration for off-market strategic cycles."""

from __future__ import annotations

import logging
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from supervisor.config import MetaSupervisorConfig, PathsConfig
from supervisor.events import EventLogger
from supervisor import state as state_utils


@dataclass
class MetaSupervisorContext:
    """Context describing current supervisor environment."""

    now: datetime
    bot_running: bool
    last_audit_reports: List[Path]


class MetaSupervisorRunner:
    """Runs Meta-Agent supervisor cycles with safety checks."""

    def __init__(
        self,
        config: MetaSupervisorConfig,
        paths: PathsConfig,
        logger: logging.Logger,
        event_logger: Optional[EventLogger],
        state_path: Path,
    ) -> None:
        self._config = config
        self._paths = paths
        self._logger = logger
        self._events = event_logger
        self._state_path = state_path
        self._meta_root = config.meta_agent_root or paths.meta_agent_root

    def load_state(self) -> state_utils.MetaSupervisorState:
        return state_utils.load_meta_supervisor_state(self._state_path)

    def save_state(self, state: state_utils.MetaSupervisorState) -> None:
        state_utils.save_meta_supervisor_state(self._state_path, state)

    def should_run(self, state: state_utils.MetaSupervisorState, ctx: MetaSupervisorContext) -> Tuple[bool, str]:
        if not self._config.enabled:
            return False, "disabled"
        if not self._meta_root or not Path(self._meta_root).exists():
            return False, "meta_agent_root_not_configured"
        if self._config.require_bot_idle and ctx.bot_running:
            return False, "bot_not_idle"
        if state.last_run_at:
            hours_since_last = (ctx.now - state.last_run_at).total_seconds() / 3600
            if hours_since_last < self._config.min_hours_between_runs:
                return False, "min_interval_not_reached"
            days_since_last = (ctx.now.date() - state.last_run_at.date()).days
            if days_since_last < self._config.frequency_days:
                return False, "frequency_interval_not_reached"
        return True, "ok"

    def _discover_supervisor_reports(self) -> List[Path]:
        if not self._meta_root:
            return []
        reports_dir = Path(self._meta_root) / "reports" / "supervisor"
        if not reports_dir.exists():
            return []
        candidates = sorted([p for p in reports_dir.glob("**/*") if p.is_file()], key=lambda p: p.stat().st_mtime, reverse=True)
        return candidates

    def _run_meta_agent_supervisor(self) -> Tuple[str, List[Path]]:
        if self._config.dry_run:
            self._logger.info("Meta-supervisor dry run; not invoking Meta-Agent.")
            self._last_run_mode = "dry_run"
            return "DRY_RUN", self._discover_supervisor_reports()

        if not self._meta_root:
            return "ERROR", []

        python_exec = self._config.python_executable or Path(sys.executable)
        cmd: List[str] = [str(python_exec)]
        meta_root_path = Path(self._meta_root)
        run_mode = "task_mode"

        supervisor_runner = meta_root_path / "supervisor_runner.py"
        meta_agent_py = meta_root_path / "meta_agent.py"

        if self._config.use_supervisor_runner and supervisor_runner.exists():
            cmd.append(str(supervisor_runner))
            run_mode = "supervisor_runner"
        elif meta_agent_py.exists():
            cmd.extend([str(meta_agent_py), "--once", "--project", self._config.project_id])
            run_mode = "task_mode"
        else:
            self._logger.error("No Meta-Agent entrypoint found in %s", meta_root_path)
            self._last_run_mode = None
            return "ERROR", []

        self._logger.info("Running Meta-Agent supervisor via: %s", " ".join(cmd))
        result = subprocess.run(cmd, cwd=meta_root_path, capture_output=True, text=True, check=False)
        if result.stdout:
            self._logger.debug("Meta-Agent stdout:\n%s", result.stdout)
        if result.stderr:
            self._logger.debug("Meta-Agent stderr:\n%s", result.stderr)

        status = "OK" if result.returncode == 0 else "ERROR"
        reports = self._discover_supervisor_reports()
        self._last_run_mode = run_mode
        return status, reports

    def run_cycle(self, ctx: MetaSupervisorContext, *, force: bool = False) -> state_utils.MetaSupervisorState:
        state = self.load_state()

        allowed, reason = self.should_run(state, ctx)
        if not allowed and not force:
            self._logger.info("Meta-supervisor skipped: %s", reason)
            if self._events:
                self._events.log_meta_supervisor_run_skipped(reason)
            state.last_status = "SKIPPED"
            state.last_reason = reason
            self.save_state(state)
            return state

        run_reason = "manual" if force else reason
        if self._events:
            self._events.log_meta_supervisor_run_started(run_reason)

        status, reports = self._run_meta_agent_supervisor()
        state.last_run_at = ctx.now
        state.last_status = status
        state.last_reason = run_reason
        state.last_reports = [str(p) for p in reports]
        state.last_run_mode = getattr(self, "_last_run_mode", None)

        if self._events:
            self._events.log_meta_supervisor_result(status, reports)

        self.save_state(state)
        return state
