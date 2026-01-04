"""Disk-backed WAL spooler for L2 events."""

from __future__ import annotations

import gzip
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import msgspec

from MarketDataHub.config import L2Config
from MarketDataHub.models import L2Envelope, encode_l2


def _never_drop_policy(
    limit: int,
    mode: str,
    current_size: int,
    refresh: Callable[[], int],
) -> int:
    """Centralized policy: never drop because of spool budget pressure."""
    if limit <= 0 or current_size <= limit:
        return current_size
    msg = f"L2 spool size {current_size}B exceeds budget {limit}B (mode={mode})"
    if mode == "warn":
        logging.warning(msg)
        return current_size
    logging.warning("%s; blocking until space is available", msg)
    size = refresh()
    while size > limit:
        time.sleep(1.0)
        size = refresh()
    return size


class L2Spooler:
    """Append-only gzip spool for L2 envelopes."""

    def __init__(
        self,
        config: L2Config,
        time_provider: Optional[Callable[[], float]] = None,
    ) -> None:
        self._config = config
        self._time_provider = time_provider or time.time
        self._lock = threading.Lock()
        self._current_hour: Optional[str] = None
        self._file: Optional[object] = None
        self._gzip: Optional[gzip.GzipFile] = None
        self._bytes_since_rotate = 0
        self._last_flush = 0.0
        self._closed = False
        self._rotate_count = 0
        self._budget_limit = self._config.max_spool_bytes
        self._budget_mode = self._config.on_budget_exceeded
        self._budget_checked_at = 0.0
        self._cached_size = 0
        self._spool_root = Path(self._config.spool_dir)

    def append(self, event: L2Envelope) -> None:
        timestamp = self._time_provider()
        hour_label = self._hour_label(timestamp)
        line = encode_l2(event) + b"\n"
        with self._lock:
            if self._closed:
                raise RuntimeError("L2Spooler is closed")
            self._enforce_budget()
            if self._should_rotate(hour_label):
                self._rotate(hour_label, timestamp)
            self._gzip.write(line)
            self._bytes_since_rotate += len(line)
            now = timestamp
            if now - self._last_flush >= self._config.flush_interval_ms / 1000.0:
                self._flush()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._flush()
            self._close_file()
            self._closed = True

    def _should_rotate(self, hour_label: str) -> bool:
        if self._current_hour != hour_label:
            return True
        if self._bytes_since_rotate >= self._config.rotate_mb * 1024 * 1024:
            return True
        return False

    def _enforce_budget(self) -> None:
        limit = self._budget_limit
        if limit <= 0:
            return
        now = self._time_provider()
        if now - self._budget_checked_at >= 1.0:
            self._cached_size = self._scan_spool_size()
            self._budget_checked_at = now

        def refresh() -> int:
            self._cached_size = self._scan_spool_size()
            self._budget_checked_at = self._time_provider()
            return self._cached_size

        self._cached_size = _never_drop_policy(
            limit, self._budget_mode, self._cached_size, refresh
        )

    def _scan_spool_size(self) -> int:
        if not self._spool_root.exists():
            return 0
        total = 0
        for path in self._spool_root.rglob("*.jsonl.gz"):
            if path.is_file():
                total += path.stat().st_size
        return total

    def _rotate(self, hour_label: str, timestamp: float) -> None:
        self._flush()
        self._close_file()
        dir_path = Path(self._config.spool_dir) / hour_label
        dir_path.mkdir(parents=True, exist_ok=True)
        fname = f"l2_{int(timestamp)}_{os.getpid()}.jsonl.gz"
        self._rotate_count += 1
        fname = f"l2_{int(timestamp)}_{os.getpid()}_{self._rotate_count}.jsonl.gz"
        file_path = dir_path / fname
        self._file = open(file_path, "ab")
        self._gzip = gzip.GzipFile(fileobj=self._file, mode="ab")
        self._bytes_since_rotate = 0
        self._current_hour = hour_label
        self._last_flush = timestamp

    def _flush(self) -> None:
        if self._gzip:
            self._gzip.flush()
        if self._file:
            self._file.flush()
            if self._config.fsync_on_rotate:
                os.fsync(self._file.fileno())
        self._last_flush = self._time_provider()

    def _close_file(self) -> None:
        if self._gzip:
            self._gzip.close()
        if self._file:
            self._file.close()
        self._file = None
        self._gzip = None
        self._bytes_since_rotate = 0

    @staticmethod
    def _hour_label(timestamp: float) -> str:
        dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d/%H")

    def spool_path(self) -> Optional[Path]:
        if not self._file:
            return None
        return Path(self._file.name)
