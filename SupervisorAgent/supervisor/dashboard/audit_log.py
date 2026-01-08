"""Dashboard audit log writer/reader."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


class DashboardAuditLogger:
    """Append-only audit log for dashboard events."""

    def __init__(self, path: Path, logger: Optional[logging.Logger] = None) -> None:
        self.path = path
        self.logger = logger or logging.getLogger(__name__)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(
        self,
        *,
        severity: str,
        component: str,
        strategy_id: Optional[str],
        symbol: Optional[str],
        event_type: str,
        payload: Dict[str, Any],
        correlation_id: Optional[str] = None,
        ts_ms: Optional[int] = None,
    ) -> None:
        record = {
            "ts_ms": int(ts_ms or time.time() * 1000),
            "severity": str(severity),
            "component": str(component),
            "strategy_id": strategy_id,
            "symbol": symbol,
            "event_type": str(event_type),
            "correlation_id": correlation_id,
            "payload": payload or {},
        }
        try:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False))
                handle.write("\n")
        except OSError as exc:
            self.logger.warning("Dashboard audit log write failed: %s", exc)

    def read(self, *, since_ts_ms: Optional[int] = None, limit: int = 200) -> list[Dict[str, Any]]:
        if limit <= 0 or not self.path.exists():
            return []
        lines = _read_last_lines(self.path, max_lines=limit * 5)
        items: list[Dict[str, Any]] = []
        for line in lines:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts_ms = payload.get("ts_ms")
            if since_ts_ms is not None and isinstance(ts_ms, (int, float)) and int(ts_ms) < since_ts_ms:
                continue
            items.append(payload)
            if len(items) >= limit:
                break
        return items


def _read_last_lines(path: Path, max_lines: int = 200, chunk_size: int = 8192, max_bytes: int = 1024 * 1024) -> list[str]:
    lines: list[str] = []
    size = 0
    with path.open("rb") as handle:
        handle.seek(0, 2)
        position = handle.tell()
        buffer = b""
        while position > 0 and len(lines) <= max_lines and size < max_bytes:
            read_size = min(chunk_size, position)
            position -= read_size
            handle.seek(position)
            data = handle.read(read_size)
            size += len(data)
            buffer = data + buffer
            while b"\n" in buffer:
                buffer, line = buffer.rsplit(b"\n", 1)
                if not line:
                    continue
                lines.append(line.decode("utf-8", errors="ignore"))
                if len(lines) >= max_lines:
                    break
            if len(lines) >= max_lines:
                break
    return lines
