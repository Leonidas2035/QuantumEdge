from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List


@dataclass
class CircuitConfig:
    failure_threshold: int
    window_s: int
    cool_down_s: int


class CircuitBreaker:
    def __init__(
        self, name: str, config: CircuitConfig, state_path: Path | None = None
    ) -> None:
        self.name = name
        self.config = config
        self.state_path = state_path
        self.failures: List[float] = []
        self.open_until: float = 0.0
        if self.state_path:
            self._load()

    def _load(self) -> None:
        if not self.state_path or not self.state_path.exists():
            return
        with open(self.state_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        state = data.get(self.name)
        if state:
            self.failures = state.get("failures", [])
            self.open_until = state.get("open_until", 0.0)

    def _persist(self) -> None:
        if not self.state_path:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        data: Dict[str, Dict[str, float | List[float]]] = {}
        if self.state_path.exists():
            with open(self.state_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        data[self.name] = {"failures": self.failures, "open_until": self.open_until}
        with open(self.state_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)

    def is_open(self, now: float) -> bool:
        return now < self.open_until

    def record_failure(self, now: float) -> None:
        window_start = now - self.config.window_s
        self.failures = [t for t in self.failures if t >= window_start]
        self.failures.append(now)
        if len(self.failures) >= self.config.failure_threshold:
            self.open_until = now + self.config.cool_down_s
        self._persist()

    def record_success(self, now: float) -> None:
        window_start = now - self.config.window_s
        self.failures = [t for t in self.failures if t >= window_start]
        self._persist()

    def snapshot(self) -> Dict[str, float | int]:
        return {
            "open_until": self.open_until,
            "failures": len(self.failures),
            "window_s": self.config.window_s,
            "cool_down_s": self.config.cool_down_s,
        }
