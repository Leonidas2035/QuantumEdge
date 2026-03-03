"""Simulated clock for replay/backtest."""

from __future__ import annotations

import time


class ReplayClock:
    def __init__(self, realtime: bool = False) -> None:
        self._now_ms = 0
        self._realtime = realtime
        self._last_wall = None

    @property
    def now_ms(self) -> int:
        return self._now_ms

    def advance_to(self, ts_ms: int) -> None:
        if ts_ms < self._now_ms:
            return
        if self._realtime:
            wall_now = time.time()
            if self._last_wall is None:
                self._last_wall = wall_now
            delta_ms = ts_ms - self._now_ms
            elapsed = (wall_now - self._last_wall) * 1000.0
            if delta_ms > elapsed:
                time.sleep((delta_ms - elapsed) / 1000.0)
            self._last_wall = time.time()
        self._now_ms = ts_ms
