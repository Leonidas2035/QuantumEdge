from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class CacheEntry:
    decision_json: str
    backend: str
    created_ts: float


class RouterCache:
    def __init__(self, path: Path, ttl_s: int) -> None:
        self.path = path
        self.ttl_s = ttl_s
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, decision TEXT, backend TEXT, created_ts REAL)"
            )
            conn.commit()

    def get(self, key: str) -> Optional[CacheEntry]:
        with sqlite3.connect(self.path) as conn:
            row = conn.execute("SELECT decision, backend, created_ts FROM cache WHERE key = ?", (key,)).fetchone()
        if not row:
            return None
        entry = CacheEntry(decision_json=row[0], backend=row[1], created_ts=row[2])
        if time.time() - entry.created_ts > self.ttl_s:
            self.delete(key)
            return None
        return entry

    def set(self, key: str, decision_json: str, backend: str) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cache (key, decision, backend, created_ts) VALUES (?, ?, ?, ?)",
                (key, decision_json, backend, time.time()),
            )
            conn.commit()

    def delete(self, key: str) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute("DELETE FROM cache WHERE key = ?", (key,))
            conn.commit()
