from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict


@dataclass
class BudgetConfig:
    max_requests_per_day: int
    max_tokens_per_day: int


class TeacherBudgets:
    def __init__(self, path: Path, config: BudgetConfig) -> None:
        self.path = path
        self.config = config
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> Dict[str, int | str]:
        if not self.path.exists():
            return {"day": "", "requests": 0, "tokens": 0}
        with open(self.path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    def _save(self, state: Dict[str, int | str]) -> None:
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(state, handle)

    def _day_bucket(self, now_utc: str) -> str:
        return now_utc[:10].replace("-", "")

    def can_use(self, now_utc: str, tokens_estimate: int) -> bool:
        if self.config.max_requests_per_day <= 0 or self.config.max_tokens_per_day <= 0:
            return False
        state = self._load()
        day = self._day_bucket(now_utc)
        if state.get("day") != day:
            reqs = 0
            toks = 0
        else:
            reqs = int(state.get("requests", 0))
            toks = int(state.get("tokens", 0))
        if reqs + 1 > self.config.max_requests_per_day:
            return False
        if toks + tokens_estimate > self.config.max_tokens_per_day:
            return False
        return True

    def record(self, now_utc: str, tokens_used: int) -> None:
        state = self._load()
        day = self._day_bucket(now_utc)
        if state.get("day") != day:
            state = {"day": day, "requests": 0, "tokens": 0}
        state["requests"] = int(state.get("requests", 0)) + 1
        state["tokens"] = int(state.get("tokens", 0)) + int(tokens_used)
        self._save(state)

    def remaining(self, now_utc: str) -> Dict[str, int]:
        state = self._load()
        day = self._day_bucket(now_utc)
        if state.get("day") != day:
            return {
                "requests": self.config.max_requests_per_day,
                "tokens": self.config.max_tokens_per_day,
            }
        reqs = int(state.get("requests", 0))
        toks = int(state.get("tokens", 0))
        return {
            "requests": max(0, self.config.max_requests_per_day - reqs),
            "tokens": max(0, self.config.max_tokens_per_day - toks),
        }
