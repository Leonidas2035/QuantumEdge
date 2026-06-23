"""Append-only action ledger for Supervisor directives."""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4
from decimal import Decimal

from hermes.supervisor.run_context import RunContext


def _json_safe(obj: Any) -> Any:
    """Convert non-JSON-serializable types to JSON-safe equivalents."""
    if dataclasses.is_dataclass(obj):
        obj = dataclasses.asdict(obj)
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(item) for item in obj]
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, set):
        return sorted(_json_safe(item) for item in obj)
    if isinstance(obj, Decimal):
        return float(obj)
    return obj


@dataclass
class ActionEntry:
    action_id: str
    ts_utc: str
    run_id: str
    action_type: str
    target: str
    payload: Dict[str, Any]
    reason_codes: list[str]
    status: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action_id": self.action_id,
            "ts_utc": self.ts_utc,
            "run_id": self.run_id,
            "action_type": self.action_type,
            "target": self.target,
            "payload": self.payload,
            "reason_codes": self.reason_codes,
            "status": self.status,
        }


class ActionLedger:
    def __init__(self, path: Path, run_context: RunContext) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.run_context = run_context

    def append(
        self,
        event_type: str,
        action_type: str,
        target: str,
        payload: Dict[str, Any],
        reason_codes: Optional[list[str]] = None,
        action_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> str:
        action_id = action_id or str(uuid4())
        # Convert payload to JSON-safe (handle Decimal, etc.)
        safe_payload = _json_safe(payload)
        entry = ActionEntry(
            action_id=action_id,
            ts_utc=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            run_id=self.run_context.run_id,
            action_type=action_type,
            target=target,
            payload=safe_payload,
            reason_codes=reason_codes or [],
            status=status or event_type,
        )
        with self.path.open("a", encoding="utf-8") as handle:
            json.dump(entry.to_dict(), handle, ensure_ascii=False)
            handle.write("\n")
        self.run_context.log_event(event_type, entry.to_dict())
        return action_id
