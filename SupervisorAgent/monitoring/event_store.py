"""In-memory telemetry store with optional persistence."""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Deque, Dict, List, Optional


class TelemetryEventStore:
    def __init__(self, max_events: int = 5000, persist_path: Optional[Path] = None) -> None:
        self._events: Deque[Dict[str, object]] = deque(maxlen=max_events)
        self._persist_path = persist_path

    def add(self, event: Dict[str, object]) -> None:
        self._events.append(event)
        if self._persist_path:
            try:
                self._persist_path.parent.mkdir(parents=True, exist_ok=True)
                with self._persist_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(event, ensure_ascii=False))
                    handle.write("\n")
            except OSError:
                return

    def recent(self, limit: int = 200) -> List[Dict[str, object]]:
        if limit <= 0:
            return []
        items = list(self._events)
        return items[-limit:]

    def size(self) -> int:
        return len(self._events)
