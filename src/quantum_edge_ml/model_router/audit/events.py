from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class AuditEvent:
    ts_utc: str
    sym: Optional[str]
    backend_used: str
    model_id: str
    mode: str
    decision_json: str
    confidence: float
    risk: str
    latency_ms: float
    prompt_hash: str
    ok: bool
    error_code: Optional[str]

    def to_fields(self) -> dict:
        return {
            "sym": self.sym or "",
            "backend_used": self.backend_used,
            "model_id": self.model_id,
            "mode": self.mode,
            "decision_json": self.decision_json,
            "confidence": self.confidence,
            "risk": self.risk,
            "latency_ms": self.latency_ms,
            "prompt_hash": self.prompt_hash,
            "ok": self.ok,
            "error_code": self.error_code or "",
        }
