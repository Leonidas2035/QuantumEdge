"""Autopilot audit log writer."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict


class AuditLogger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, record: Dict[str, Any]) -> None:
        payload = dict(record)
        payload.setdefault("ts_utc", datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
        try:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload) + "\n")
        except Exception:
            return
