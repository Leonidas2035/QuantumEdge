from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Dict

if TYPE_CHECKING:
    from quantum_edge_core.contracts.optimization import UsageRecordV1
    from quantum_edge_ml.model_router.usage_ledger import UsageLedger


@dataclass
class BudgetConfig:
    max_requests_per_day: int
    max_tokens_per_day: int


class TeacherBudgets:
    """Backward-compatible daily teacher budget tracker with optional UsageLedger sync."""

    def __init__(
        self,
        path: Path,
        config: BudgetConfig,
        ledger: UsageLedger | None = None,
    ) -> None:
        self.path = path
        self.config = config
        self.ledger = ledger
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
        if self.ledger is not None:
            from quantum_edge_core.contracts.optimization import (
                RequestStatus,
                TransportChannel,
                UsageRecordV1,
            )

            self.ledger.record_usage(
                UsageRecordV1(
                    record_id=f"bgt-{uuid.uuid4().hex[:16]}",
                    decision_id=f"budget-{day}",
                    stage="proxy_legacy",
                    transport=TransportChannel.AGY_CLI_PROXY,
                    model_provider_id="teacher_legacy",
                    request_status=RequestStatus.OK,
                    total_tokens=int(tokens_used),
                    latency_ms=0.0,
                    timeout_ms=0.0,
                )
            )

    def record_usage(self, rec: UsageRecordV1) -> None:
        """Forward a native UsageRecordV1 to both the ledger and daily JSON counter."""
        if self.ledger is not None:
            self.ledger.record_usage(rec)
        tokens_used = int(rec.total_tokens or 0)
        self.record(rec.ts_utc.isoformat(), tokens_used)

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
