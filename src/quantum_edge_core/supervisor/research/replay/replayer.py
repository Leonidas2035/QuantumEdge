"""Offline event replay runner."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from .adapters import MarketEvent


@dataclass
class ReplayConfig:
    speed: float = 0.0  # 0 = no sleep, >0 = real-time multiplier


def replay_events(
    events: Iterable[MarketEvent],
    handler: Callable[[MarketEvent], None],
    config: Optional[ReplayConfig] = None,
) -> None:
    cfg = config or ReplayConfig()
    last_ts: Optional[int] = None
    for event in events:
        if cfg.speed and last_ts is not None:
            delay = max(event.ts - last_ts, 0) / 1000.0 / cfg.speed
            if delay > 0:
                time.sleep(delay)
        handler(event)
        last_ts = event.ts
