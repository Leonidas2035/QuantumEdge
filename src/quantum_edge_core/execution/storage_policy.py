"""RAM-First / SSD-Wear Protection storage policy for QuantumEdge Hybrid.

All ephemeral market streams, ring buffers, temporary context dumps, WebP
rendering buffers, and pre-flush logs MUST stay in RAM (32 GB host RAM /
``/dev/shm/quantumedge`` tmpfs). Only critical durable state is written to SSD
via SQLite WAL mode or batched JSONL archivers.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import threading
import time
from collections import deque
from enum import Enum
from pathlib import Path
from typing import Any, Generic, Iterable, TypeVar

T = TypeVar("T")

_DEFAULT_SHM_ROOT = Path("/dev/shm/quantumedge")
_SQLITE_MMAP_BYTES = 268_435_456  # 256 MiB


class StorageTier(str, Enum):
    """Canonical storage tiers for QuantumEdge runtime artifacts."""

    RAM_EPHEMERAL = "ram_ephemeral"
    RAM_BUFFERED_ARCHIVE = "ram_buffered_archive"
    SSD_DURABLE_WAL = "ssd_durable_wal"


# Canonical SSD_DURABLE_WAL relative paths (anchored at project runtime/)
CANONICAL_SSD_DURABLE_PATHS: dict[str, Path] = {
    "lean_orders": Path("runtime/lean_orders.sqlite3"),
    "usage_ledger": Path("runtime/usage_ledger.sqlite3"),
    "thesis_memory": Path("runtime/thesis_memory.sqlite3"),
    "lean_trade_journal": Path("runtime/lean_trade_journal.jsonl"),
}


def get_ram_tmp_dir(subdir: str = "") -> Path:
    """Return a writable RAM-backed temporary directory.

    Uses ``/dev/shm/quantumedge`` by default and falls back to
    ``<tempfile.gettempdir()>/quantumedge_ram`` only when ``/dev/shm`` is
    unavailable or non-writable.
    """
    shm_parent = Path("/dev/shm")
    if shm_parent.is_dir() and os.access(shm_parent, os.W_OK | os.X_OK):
        base = _DEFAULT_SHM_ROOT
    else:
        base = Path(tempfile.gettempdir()) / "quantumedge_ram"

    target = base / subdir if subdir else base
    target.mkdir(parents=True, exist_ok=True)
    return target


def apply_sqlite_wal_pragmas(conn: sqlite3.Connection) -> None:
    """Configure SQLite connection for low SSD-wear WAL + RAM temp storage."""
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA temp_store=MEMORY;")
    conn.execute(f"PRAGMA mmap_size={_SQLITE_MMAP_BYTES};")


class RingMarketBuffer(Generic[T]):
    """Fixed-capacity in-memory ring buffer for streaming OHLCV, L2, and ticks."""

    def __init__(self, maxlen: int) -> None:
        if maxlen <= 0:
            raise ValueError("maxlen must be positive")
        self._maxlen = maxlen
        self._buf: deque[T] = deque(maxlen=maxlen)

    @property
    def maxlen(self) -> int:
        return self._maxlen

    def append(self, item: T) -> None:
        self._buf.append(item)

    def extend(self, items: Iterable[T]) -> None:
        self._buf.extend(items)

    def snapshot(self) -> list[T]:
        """Return a shallow list copy of buffered items in insertion order."""
        return list(self._buf)

    def latest(self) -> T | None:
        return self._buf[-1] if self._buf else None

    def clear(self) -> None:
        self._buf.clear()

    def __len__(self) -> int:
        return len(self._buf)


class BufferedJsonlArchiver:
    """Buffers JSONL records in RAM and flushes to SSD in batches.

    Does NOT create or touch the target SSD file until ``flush_max_items`` is
    reached, ``flush_interval_sec`` elapses on an append, or ``flush()`` /
    ``close()`` is explicitly called.
    """

    def __init__(
        self,
        path: Path | str,
        *,
        flush_max_items: int = 100,
        flush_interval_sec: float = 30.0,
    ) -> None:
        if flush_max_items <= 0:
            raise ValueError("flush_max_items must be positive")
        if flush_interval_sec <= 0:
            raise ValueError("flush_interval_sec must be positive")
        self.path = Path(path)
        self.flush_max_items = flush_max_items
        self.flush_interval_sec = flush_interval_sec
        self._buffer: list[str] = []
        self._last_flush_ts: float = time.monotonic()
        self._lock = threading.Lock()

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._buffer)

    def append(self, record: dict[str, Any]) -> bool:
        """Append a dict record to RAM buffer. Returns True if a batch flush ran."""
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            self._buffer.append(line)
            now = time.monotonic()
            should_flush = (
                len(self._buffer) >= self.flush_max_items
                or (now - self._last_flush_ts) >= self.flush_interval_sec
            )
            if should_flush:
                self._flush_locked()
                return True
            return False

    def flush(self) -> int:
        """Flush all pending records to SSD. Returns number of flushed lines."""
        with self._lock:
            return self._flush_locked()

    def _flush_locked(self) -> int:
        if not self._buffer:
            self._last_flush_ts = time.monotonic()
            return 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = "\n".join(self._buffer) + "\n"
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(payload)
        flushed = len(self._buffer)
        self._buffer.clear()
        self._last_flush_ts = time.monotonic()
        return flushed

    def close(self) -> int:
        return self.flush()

    def __enter__(self) -> BufferedJsonlArchiver:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()


__all__ = [
    "BufferedJsonlArchiver",
    "CANONICAL_SSD_DURABLE_PATHS",
    "RingMarketBuffer",
    "StorageTier",
    "apply_sqlite_wal_pragmas",
    "get_ram_tmp_dir",
]
