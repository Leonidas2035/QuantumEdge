"""Remediation actions for autopilot."""

from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import dataclass
import uuid
from pathlib import Path
from typing import Deque, Optional

from supervisor.autopilot.audit import AuditLogger
from supervisor.autopilot.policy_manager import PolicyManager


@dataclass
class ActionResult:
    action: str
    applied: bool
    reason: str


class RateLimiter:
    def __init__(self, max_per_hour: int) -> None:
        self.max_per_hour = max(int(max_per_hour), 1)
        self.timestamps: Deque[float] = deque()

    def allow(self, now: Optional[float] = None) -> bool:
        now = now or time.time()
        cutoff = now - 3600
        while self.timestamps and self.timestamps[0] < cutoff:
            self.timestamps.popleft()
        if len(self.timestamps) >= self.max_per_hour:
            return False
        self.timestamps.append(now)
        return True


class RemediationManager:
    def __init__(
        self,
        app,
        policy_manager: PolicyManager,
        audit: AuditLogger,
        kill_switch_path: Path,
        restart_max_per_hour: int,
        restart_cooldown_sec: int,
        max_actions_per_hour: int,
        disable_entries_on_degrade: bool,
    ) -> None:
        self.app = app
        self.policy_manager = policy_manager
        self.audit = audit
        self.kill_switch_path = kill_switch_path
        self.restart_limiter = RateLimiter(restart_max_per_hour)
        self.action_limiter = RateLimiter(max_actions_per_hour)
        self.restart_cooldown_sec = max(int(restart_cooldown_sec), 10)
        self._last_restart_ts = 0.0
        self.disable_entries_on_degrade = bool(disable_entries_on_degrade)

    def degrade_to_shadow(
        self, reason: str, evidence: Optional[dict] = None
    ) -> ActionResult:
        if not self._allow_action("DEGRADE_TO_SHADOW"):
            return ActionResult("DEGRADE_TO_SHADOW", False, "AP_ACTION_RATE_LIMIT")
        if self.disable_entries_on_degrade:
            self._write_kill_switch(True, reason)
        self._log("DEGRADE_TO_SHADOW", True, reason, evidence=evidence)
        return ActionResult("DEGRADE_TO_SHADOW", True, reason)

    def disable_entries(
        self, reason: str, evidence: Optional[dict] = None
    ) -> ActionResult:
        if not self._allow_action("DISABLE_ENTRIES"):
            return ActionResult("DISABLE_ENTRIES", False, "AP_ACTION_RATE_LIMIT")
        self._write_kill_switch(True, reason)
        self._log("DISABLE_ENTRIES", True, reason, evidence=evidence)
        return ActionResult("DISABLE_ENTRIES", True, reason)

    def halt_trading(
        self, reason: str, evidence: Optional[dict] = None
    ) -> ActionResult:
        if not self._allow_action("HALT_TRADING"):
            return ActionResult("HALT_TRADING", False, "AP_ACTION_RATE_LIMIT")
        self._write_kill_switch(True, reason)
        self._log("HALT_TRADING", True, reason, evidence=evidence)
        return ActionResult("HALT_TRADING", True, reason)

    def restart_quantumedge(
        self, reason: str, evidence: Optional[dict] = None
    ) -> ActionResult:
        if not self._allow_action("RESTART_QUANTEDGE"):
            return ActionResult("RESTART_QUANTEDGE", False, "AP_ACTION_RATE_LIMIT")
        now = time.time()
        if not self.restart_limiter.allow(now):
            self._log("RESTART_QUANTEDGE", False, "AP_RESTART_LOOP_GUARD")
            return ActionResult("RESTART_QUANTEDGE", False, "AP_RESTART_LOOP_GUARD")
        if now - self._last_restart_ts < self.restart_cooldown_sec:
            self._log("RESTART_QUANTEDGE", False, "AP_RESTART_LOOP_GUARD")
            return ActionResult("RESTART_QUANTEDGE", False, "AP_RESTART_LOOP_GUARD")
        self._last_restart_ts = now
        self.app.restart_bot()
        self._log("RESTART_QUANTEDGE", True, reason, evidence=evidence)
        return ActionResult("RESTART_QUANTEDGE", True, reason)

    def rollback_policy(
        self, reason: str, evidence: Optional[dict] = None
    ) -> ActionResult:
        if not self._allow_action("ROLLBACK_POLICY"):
            return ActionResult("ROLLBACK_POLICY", False, "AP_ACTION_RATE_LIMIT")
        path = self.policy_manager.rollback(reason)
        applied = bool(path)
        self._log("ROLLBACK_POLICY", applied, reason, evidence=evidence)
        return ActionResult("ROLLBACK_POLICY", applied, reason)

    def _write_kill_switch(self, enabled: bool, reason: str) -> None:
        self.kill_switch_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"enabled": bool(enabled), "reason": reason, "ts": int(time.time())}
        self.kill_switch_path.write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )

    def _log(
        self, action: str, applied: bool, reason: str, evidence: Optional[dict] = None
    ) -> None:
        payload = {
            "action": action,
            "applied": applied,
            "reason": reason,
            "correlation_id": str(uuid.uuid4()),
        }
        if evidence:
            payload["evidence"] = evidence
        self.audit.log(payload)

    def _allow_action(self, action: str) -> bool:
        if self.action_limiter.allow():
            return True
        self._log(action, False, "AP_ACTION_RATE_LIMIT")
        return False
