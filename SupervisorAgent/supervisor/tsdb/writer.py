"""Background TSDB writer with buffering."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import List, Optional

from supervisor.tsdb.base import Point, TimeseriesStore


class TsdbWriter:
    """Buffer points and flush in a background thread."""

    def __init__(
        self,
        store: TimeseriesStore,
        flush_interval_seconds: int = 2,
        batch_size: int = 500,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.store = store
        self.flush_interval = flush_interval_seconds
        self.batch_size = batch_size
        self.logger = logger or logging.getLogger(__name__)
        self._buffer: List[Point] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.last_write_at: Optional[datetime] = None

    def start(self) -> None:
        if self._thread:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        self.flush()

    def enqueue(self, points: List[Point]) -> None:
        if not points:
            return
        with self._lock:
            self._buffer.extend(points)
            if len(self._buffer) >= self.batch_size:
                self._flush_locked()

    def flush(self) -> None:
        with self._lock:
            self._flush_locked()

    def _flush_locked(self) -> None:
        if not self._buffer:
            return
        batch = self._buffer[:]
        self._buffer.clear()
        try:
            self.store.write_points(batch)
            self.last_write_at = datetime.now(timezone.utc)
        except Exception as exc:  # pylint: disable=broad-except
            self.logger.warning("TSDB flush failed: %s", exc)

    def _run(self) -> None:
        while not self._stop.is_set():
            time.sleep(self.flush_interval)
            self.flush()

    @property
    def queue_depth(self) -> int:
        with self._lock:
            return len(self._buffer)
