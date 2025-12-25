"""Simple ClickHouse HTTP TSDB backend."""

from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request
from typing import Dict, Optional

from supervisor.tsdb.base import Point, TimeseriesStore


class ClickHouseTimeseriesStore(TimeseriesStore):
    """Writes points to ClickHouse via HTTP JSONEachRow."""

    def __init__(
        self,
        url: str,
        database: str,
        user: str,
        password: str,
        table_prefix: str,
        retry_cfg: Optional[Dict[str, int]] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.url = url.rstrip("/")
        self.database = database
        self.user = user
        self.password = password
        self.table = f"{table_prefix}tsdb_points"
        self.logger = logger or logging.getLogger(__name__)
        retry_cfg = retry_cfg or {}
        self.max_retries = int(retry_cfg.get("max_retries", 3))
        self.base_backoff_ms = int(retry_cfg.get("base_backoff_ms", 200))
        self.max_backoff_ms = int(retry_cfg.get("max_backoff_ms", 5000))

    def write_points(self, points: list[Point]) -> None:
        if not points:
            return
        rows = []
        for p in points:
            rows.append(
                {
                    "ts": p.ts.isoformat(),
                    "measurement": p.measurement,
                    "tags": p.tags,
                    "fields": p.fields,
                }
            )
        payload = "\n".join(json.dumps(r) for r in rows).encode("utf-8")
        query = f"INSERT INTO {self.table} FORMAT JSONEachRow"
        attempt = 0
        backoff = self.base_backoff_ms / 1000.0
        while True:
            try:
                req = urllib.request.Request(f"{self.url}/?database={self.database}&query={urllib.parse.quote(query)}", data=payload, method="POST")
                if self.user:
                    creds = f"{self.user}:{self.password or ''}".encode("utf-8")
                    import base64

                    req.add_header("Authorization", "Basic " + base64.b64encode(creds).decode("utf-8"))
                req.add_header("Content-Type", "application/json")
                with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
                    if resp.status >= 300:
                        raise RuntimeError(f"ClickHouse HTTP status {resp.status}")
                return
            except Exception as exc:  # pylint: disable=broad-except
                attempt += 1
                if attempt > self.max_retries:
                    self.logger.warning("ClickHouse write failed after retries: %s", exc)
                    return
                time.sleep(backoff)
                backoff = min(self.max_backoff_ms / 1000.0, backoff * 2)

    def flush(self) -> None:
        return
