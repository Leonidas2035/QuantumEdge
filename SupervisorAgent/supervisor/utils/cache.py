"""Simple TTL cache utilities."""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional, Tuple


class TtlCache:
    """In-memory TTL cache keyed by arbitrary hashable keys."""

    def __init__(self, ttl_seconds: int) -> None:
        self.ttl = max(1, ttl_seconds)
        self._data: Dict[Any, Tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: Any) -> Optional[Any]:
        now = time.time()
        with self._lock:
            entry = self._data.get(key)
            if not entry:
                return None
            ts, value = entry
            if now - ts > self.ttl:
                self._data.pop(key, None)
                return None
            return value

    def set(self, key: Any, value: Any) -> None:
        with self._lock:
            self._data[key] = (time.time(), value)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
