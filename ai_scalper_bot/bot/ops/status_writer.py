"""Periodic bot status writer for ops integrations."""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional


class BotStatusWriter:
    """Write status to JSON atomically with throttling."""

    def __init__(self, path: Path, interval_seconds: float = 2.0) -> None:
        self.path = path
        self.interval = max(interval_seconds, 0.5)
        self._last_write = 0.0
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def update(self, payload: Dict[str, Any]) -> None:
        now = time.time()
        if now - self._last_write < self.interval:
            return
        tmp_fd, tmp_path = tempfile.mkstemp(prefix="bot_status_", suffix=".json", dir=str(self.path.parent))
        try:
            with open(tmp_fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
            Path(tmp_path).replace(self.path)
            self._last_write = now
        except Exception:
            # fail silently; ops status is best-effort
            return

    def flush(self, payload: Optional[Dict[str, Any]] = None) -> None:
        """Force a write immediately."""
        if payload is None:
            return
        self._last_write = 0.0
        self.update(payload)
