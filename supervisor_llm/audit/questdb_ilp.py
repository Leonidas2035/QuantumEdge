from __future__ import annotations

import json
import os
from pathlib import Path

from supervisor_llm.audit.events import AuditEvent


class QuestDBAuditWriter:
    def __init__(self) -> None:
        self.table = os.environ.get("QDB_DECISIONS_TABLE", "llm_decisions")
        self.host = os.environ.get("QDB_ILP_HOST", "127.0.0.1")
        self.port = int(os.environ.get("QDB_ILP_PORT", "9009"))
        self.fallback_path = Path(__file__).resolve().parents[1] / "runtime" / "audit_fallback.jsonl"

    def write(self, event: AuditEvent) -> None:
        try:
            from questdb.ingress import Sender
        except Exception:
            self._fallback(event, reason="ilp_missing")
            return

        try:
            with Sender(host=self.host, port=self.port) as sender:
                sender.row(self.table, symbols={"sym": event.sym or ""}, columns=event.to_fields())
                sender.flush()
        except Exception:
            self._fallback(event, reason="ilp_error")

    def _fallback(self, event: AuditEvent, reason: str) -> None:
        payload = {"reason": reason, **event.to_fields(), "ts_utc": event.ts_utc}
        self.fallback_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.fallback_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + "\n")
