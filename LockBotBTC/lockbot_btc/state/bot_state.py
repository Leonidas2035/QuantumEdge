"""Bot state container for LockBotBTC."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Optional


@dataclass
class BotState:
    bot_id: str
    symbol: str
    mode: str = "IDLE"
    regime: str = "UNKNOWN"
    ddn_profile: str = "neutral"
    ddn_target: float = 0.0
    ddn_band_low: float = -0.10
    ddn_band_high: float = 0.10
    state_version: int = 0
    last_error: Optional[str] = None
    last_ddn_verdict: Optional[str] = None
    last_ddn_reasons: list[str] = field(default_factory=list)
    last_ddn_step_qty: Optional[float] = None
    last_ddn_cost_bps: Optional[float] = None
    last_order_plans: Deque[dict] = field(default_factory=deque)
    _cmd_cache: Deque[str] = field(default_factory=deque)
    _cmd_cache_size: int = 256

    def configure_cache(self, size: int) -> None:
        self._cmd_cache_size = max(int(size), 1)

    def is_duplicate(self, cmd_id: str) -> bool:
        return cmd_id in self._cmd_cache

    def remember_cmd(self, cmd_id: str) -> None:
        if cmd_id in self._cmd_cache:
            return
        self._cmd_cache.append(cmd_id)
        while len(self._cmd_cache) > self._cmd_cache_size:
            self._cmd_cache.popleft()

    def bump_state(self) -> None:
        self.state_version += 1

    def record_decision(self, verdict: str, reasons: list[str], step_qty: Optional[float], cost_bps: Optional[float], plans: list[dict]) -> None:
        self.last_ddn_verdict = verdict
        self.last_ddn_reasons = list(reasons)
        self.last_ddn_step_qty = step_qty
        self.last_ddn_cost_bps = cost_bps
        for plan in plans:
            self.last_order_plans.append(plan)
        while len(self.last_order_plans) > 20:
            self.last_order_plans.popleft()
