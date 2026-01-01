from __future__ import annotations

import time
from typing import Any, Dict, List

import requests


class QuestDbClient:
    """HTTP /exec client for QuestDB with retries."""

    def __init__(
        self,
        query_url: str,
        timeout: float = 3.0,
        max_retries: int = 3,
        base_backoff_s: float = 0.2,
        max_backoff_s: float = 5.0,
    ) -> None:
        self.query_url = query_url.rstrip("/")
        self.timeout = max(timeout, 0.1)
        self.max_retries = max(int(max_retries), 0)
        self.base_backoff_s = max(base_backoff_s, 0.05)
        self.max_backoff_s = max(max_backoff_s, self.base_backoff_s)

    def query(self, sql: str) -> List[Dict[str, Any]]:
        attempt = 0
        backoff = self.base_backoff_s
        while True:
            try:
                resp = requests.get(
                    self.query_url,
                    params={"query": sql, "fmt": "json"},
                    timeout=self.timeout,
                )
                if resp.status_code >= 300:
                    raise RuntimeError(f"QuestDB query failed: {resp.status_code} {resp.text}")
                payload = resp.json()
                return _rows_from_questdb(payload)
            except Exception:
                attempt += 1
                if attempt > self.max_retries:
                    raise
                time.sleep(backoff)
                backoff = min(backoff * 2, self.max_backoff_s)


def _rows_from_questdb(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    cols = [col.get("name") for col in payload.get("columns", [])]
    rows = []
    for row in payload.get("dataset", []) or []:
        rows.append({cols[i]: row[i] if i < len(row) else None for i in range(len(cols))})
    return rows
