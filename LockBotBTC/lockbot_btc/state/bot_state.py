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
    state_version: int = 0
    last_error: Optional[str] = None
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

