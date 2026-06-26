"""Process specification models for SupervisorAgent orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class RestartPolicySpec:
    enabled: bool = True
    max_retries: int = 5
    backoff_s: List[float] = field(default_factory=lambda: [1, 2, 4, 8, 16])
    cooldown_s: float = 30.0

    def backoff_for_attempt(self, attempt: int) -> float:
        if attempt <= 0:
            return 0.0
        index = min(attempt - 1, len(self.backoff_s) - 1)
        try:
            delay = float(self.backoff_s[index])
        except (TypeError, ValueError):
            delay = 1.0
        return max(delay, 0.0)


@dataclass
class HealthCheckSpec:
    type: str = "none"  # none|http|tcp
    url: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    timeout_s: float = 2.0


@dataclass
class ProcessSpec:
    name: str
    enabled: bool
    cwd: Path
    cmd: List[str]
    env: Dict[str, str] = field(default_factory=dict)
    ports: List[int] = field(default_factory=list)
    healthcheck: HealthCheckSpec = field(default_factory=HealthCheckSpec)
    restart: RestartPolicySpec = field(default_factory=RestartPolicySpec)


@dataclass
class ProcessStatus:
    name: str
    pid: Optional[int]
    is_running: bool
    state: str
    last_start_ts: Optional[str]
    last_exit_code: Optional[int]
    last_health: Optional[str]
    last_health_ts: Optional[str]
    retries: int
    last_error: Optional[str]

    def to_dict(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "pid": self.pid,
            "is_running": self.is_running,
            "state": self.state,
            "last_start_ts": self.last_start_ts,
            "last_exit_code": self.last_exit_code,
            "last_health": self.last_health,
            "last_health_ts": self.last_health_ts,
            "retries": self.retries,
            "last_error": self.last_error,
        }


def _iso(ts: Optional[datetime]) -> Optional[str]:
    return ts.isoformat() if ts else None
