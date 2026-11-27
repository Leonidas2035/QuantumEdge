from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn, Optional


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
            row = conn.execute(
                "SELECT decision, backend, created_ts FROM cache WHERE key = ?", (key,)
            ).fetchone()
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


class ImplicitCacheEvictionMonitor:
    """Monitors Gemini 4 Implicit Caching hit/miss telemetry and serial evictions."""

    def __init__(self, max_consecutive_stage2_misses: int = 3) -> None:
        if max_consecutive_stage2_misses <= 0:
            raise ValueError("max_consecutive_stage2_misses must be positive")
        self.max_consecutive_stage2_misses = max_consecutive_stage2_misses
        self._consecutive_stage2_misses: int = 0
        self._total_prompt_tokens: int = 0
        self._total_cache_read_tokens: int = 0
        self._total_observations: int = 0
        self._cache_hit_observations: int = 0

    @property
    def consecutive_stage2_misses(self) -> int:
        return self._consecutive_stage2_misses

    @property
    def total_prompt_tokens(self) -> int:
        return self._total_prompt_tokens

    @property
    def total_cache_read_tokens(self) -> int:
        return self._total_cache_read_tokens

    @property
    def cache_token_fraction(self) -> float:
        if self._total_prompt_tokens <= 0:
            return 0.0
        return round(self._total_cache_read_tokens / self._total_prompt_tokens, 4)

    @property
    def cache_request_hit_rate(self) -> float:
        if self._total_observations <= 0:
            return 0.0
        return round(self._cache_hit_observations / self._total_observations, 4)

    @property
    def alert_active(self) -> bool:
        return self._consecutive_stage2_misses >= self.max_consecutive_stage2_misses

    def reset_misses(self) -> None:
        self._consecutive_stage2_misses = 0

    def observe(
        self,
        prompt_tokens: int | None,
        cache_read_tokens: int | None,
        *,
        stage: str = "stage2_drill",
    ) -> bool:
        """Record an observation and return True if serial Stage-2 eviction alert is active."""
        if prompt_tokens is not None and prompt_tokens > 0:
            self._total_prompt_tokens += int(prompt_tokens)
        if cache_read_tokens is not None:
            self._total_observations += 1
            if cache_read_tokens > 0:
                self._total_cache_read_tokens += int(cache_read_tokens)
                self._cache_hit_observations += 1

        if stage == "stage2_drill" and cache_read_tokens is not None:
            if cache_read_tokens == 0:
                self._consecutive_stage2_misses += 1
            else:
                self._consecutive_stage2_misses = 0

        return self.alert_active

    @staticmethod
    def reject_explicit_caching() -> NoReturn:
        """Explicit context caching API is unsupported on Gemini 4 (returns HTTP 400)."""
        raise ValueError(
            "Explicit caching API is unsupported on Gemini 4; use Implicit Caching via PrefixContextAssembler."
        )
