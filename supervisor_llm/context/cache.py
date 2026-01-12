from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


@dataclass
class CacheEntry:
    value: Any
    ts: float


class ContextCache:
    def __init__(self, ttl_s: float) -> None:
        self.ttl_s = ttl_s
        self._store: Dict[Tuple[str, int], CacheEntry] = {}

    def get(self, symbol: str, lookback_m: int) -> Optional[Any]:
        key = (symbol, lookback_m)
        entry = self._store.get(key)
        if not entry:
            return None
        if time.time() - entry.ts > self.ttl_s:
            self._store.pop(key, None)
            return None
        return entry.value

    def set(self, symbol: str, lookback_m: int, value: Any) -> None:
        self._store[(symbol, lookback_m)] = CacheEntry(value=value, ts=time.time())
