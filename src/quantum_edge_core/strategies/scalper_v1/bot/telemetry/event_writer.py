"""Local JSONL event writer with size-based rotation."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict


class EventWriter:
    def __init__(self, path: Path, max_size_mb: int = 5, backup_count: int = 3) -> None:
        self.path = Path(path)
        self.max_bytes = max(int(max_size_mb), 1) * 1024 * 1024
        self.backup_count = max(int(backup_count), 1)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _rotate(self) -> None:
        if not self.path.exists():
            return
        try:
            if self.path.stat().st_size < self.max_bytes:
                return
        except Exception:
            return
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        rotated = self.path.with_name(f"{self.path.stem}.{ts}{self.path.suffix}")
        try:
            self.path.replace(rotated)
        except Exception:
            return
        self._prune()

    def _prune(self) -> None:
        try:
            candidates = sorted(
                self.path.parent.glob(f"{self.path.stem}.*{self.path.suffix}"),
                key=lambda p: p.stat().st_mtime,
            )
        except Exception:
            return
        if len(candidates) <= self.backup_count:
            return
        for old in candidates[: -self.backup_count]:
            try:
                old.unlink()
            except Exception:
                continue

    def write(self, event: Dict[str, Any]) -> None:
        try:
            self._rotate()
            payload = dict(event)
            payload.setdefault("event_version", "events.v1")
            payload.setdefault("ts_utc", datetime.now(timezone.utc).isoformat())
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, separators=(",", ":")) + "\n")
        except Exception:
            return
