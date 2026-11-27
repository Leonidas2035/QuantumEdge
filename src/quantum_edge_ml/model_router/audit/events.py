from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from quantum_edge_core.contracts.optimization import TransportChannel, UsageRecordV1


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

    def to_usage_record(
        self,
        *,
        decision_id: str | None = None,
        transport: Any = None,
        timeout_ms: float = 0.0,
    ) -> UsageRecordV1:
        """Convert a legacy AuditEvent into a canonical UsageRecordV1 (tokens=UNKNOWN)."""
        from quantum_edge_core.contracts.optimization import (
            RequestStatus,
            TransportChannel,
            UsageRecordV1,
        )

        resolved_transport = transport or TransportChannel.AGY_CLI_PROXY
        status = RequestStatus.OK if self.ok else RequestStatus.ERROR
        if self.error_code and "timeout" in self.error_code.lower():
            status = RequestStatus.TIMEOUT
        return UsageRecordV1(
            record_id=f"aud-{uuid.uuid4().hex[:16]}",
            decision_id=decision_id or f"legacy-{self.prompt_hash[:12]}",
            stage="proxy_legacy",
            transport=resolved_transport,
            model_provider_id=self.model_id,
            request_status=status,
            error_code=self.error_code,
            latency_ms=float(self.latency_ms),
            timeout_ms=float(timeout_ms),
            is_censored=(status == RequestStatus.TIMEOUT),
            context_hash=self.prompt_hash,
            snapshot_id=self.sym,
        )

    @classmethod
    def from_usage_record(
        cls,
        rec: UsageRecordV1,
        *,
        sym: Optional[str] = None,
        mode: str = "direct",
        decision_json: str = "{}",
        confidence: float = 0.0,
        risk: str = "LOW",
    ) -> AuditEvent:
        """Build a legacy AuditEvent view from a canonical UsageRecordV1."""
        from quantum_edge_core.contracts.optimization import RequestStatus

        return cls(
            ts_utc=rec.ts_utc.isoformat(),
            sym=sym or rec.snapshot_id,
            backend_used=rec.transport.value,
            model_id=rec.model_provider_id,
            mode=mode,
            decision_json=decision_json,
            confidence=confidence,
            risk=risk,
            latency_ms=rec.latency_ms,
            prompt_hash=rec.context_hash or rec.record_id,
            ok=(rec.request_status == RequestStatus.OK),
            error_code=rec.error_code,
        )
