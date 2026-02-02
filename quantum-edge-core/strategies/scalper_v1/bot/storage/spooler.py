from __future__ import annotations

import gzip
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Optional


@dataclass
class SpoolStats:
    batches: int = 0
    events: int = 0
    bytes_written: int = 0


class Spooler:
    """Spool failed batches to compressed JSONL for later replay."""

    def __init__(
        self,
        base_dir: Path,
        max_bytes: int,
        retention_days: int,
        max_file_bytes: int,
        rotation_minutes: int,
    ) -> None:
        self.base_dir = Path(base_dir)
        self.max_bytes = max(int(max_bytes), 0)
        self.retention_days = max(int(retention_days), 0)
        self.max_file_bytes = max(int(max_file_bytes), 1024 * 1024)
        self.rotation_minutes = max(int(rotation_minutes), 1)
        self._current_path: Optional[Path] = None
        self._current_started = 0.0
        self._current_bytes = 0
        self.stats = SpoolStats()

    def _current_target(self) -> Path:
        now = datetime.now(timezone.utc)
        directory = self.base_dir / now.strftime("%Y-%m-%d") / now.strftime("%H")
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"spool_{now.strftime('%Y%m%d_%H%M%S')}.jsonl.gz"

    def _ensure_target(self) -> Path:
        now = time.time()
        if self._current_path is None:
            self._current_path = self._current_target()
            self._current_started = now
            self._current_bytes = 0
            return self._current_path
        too_old = (now - self._current_started) >= (self.rotation_minutes * 60)
        too_big = self._current_bytes >= self.max_file_bytes
        if too_old or too_big:
            self._current_path = self._current_target()
            self._current_started = now
            self._current_bytes = 0
        return self._current_path

    def spool_events(self, events: Iterable[Dict]) -> Optional[Path]:
        if not events:
            return None
        path = self._ensure_target()
        lines = []
        for event in events:
            try:
                lines.append(json.dumps(event, separators=(",", ":"), default=str))
            except Exception:
                continue
        if not lines:
            return None
        payload = "\n".join(lines) + "\n"
        try:
            with gzip.open(path, "at", encoding="utf-8") as handle:
                handle.write(payload)
        except Exception:
            return None
        self._current_bytes += len(payload.encode("utf-8"))
        self.stats.batches += 1
        self.stats.events += len(lines)
        self.stats.bytes_written += len(payload.encode("utf-8"))
        self._enforce_limits()
        return path

    def _enforce_limits(self) -> None:
        self._prune_retention()
        if self.max_bytes <= 0:
            return
        files = sorted(self.base_dir.rglob("*.jsonl.gz"), key=lambda p: p.stat().st_mtime)
        total = 0
        sizes = {}
        for f in files:
            try:
                sizes[f] = f.stat().st_size
                total += sizes[f]
            except Exception:
                continue
        if total <= self.max_bytes:
            return
        for f in files:
            try:
                f.unlink()
            except Exception:
                continue
            total -= sizes.get(f, 0)
            if total <= self.max_bytes:
                break

    def _prune_retention(self) -> None:
        if self.retention_days <= 0:
            return
        cutoff = time.time() - (self.retention_days * 86400)
        for f in self.base_dir.rglob("*.jsonl.gz"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
            except Exception:
                continue
