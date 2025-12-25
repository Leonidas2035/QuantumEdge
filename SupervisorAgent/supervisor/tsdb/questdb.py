"""QuestDB ILP store."""

from __future__ import annotations

import logging
import time
import urllib.request
from typing import Dict, Optional

from supervisor.tsdb.base import Point, TimeseriesStore


def _escape(val: str) -> str:
    return val.replace(" ", "\\ ").replace(",", "\\,")


def _encode_field(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return f"{value}i"
    if isinstance(value, float):
        return str(value)
    if value is None:
        return '"null"'
    # strings
    return f'"{str(value).replace(chr(34), r"\"")}"'


def point_to_line(point: Point) -> str:
    tags = ",".join(f"{_escape(k)}={_escape(v)}" for k, v in point.tags.items())
    fields = ",".join(f"{_escape(k)}={_encode_field(v)}" for k, v in point.fields.items())
    ts_ns = int(point.ts.timestamp() * 1_000_000_000)
    if tags:
        return f"{_escape(point.measurement)},{tags} {fields} {ts_ns}"
    return f"{_escape(point.measurement)} {fields} {ts_ns}"


class QuestDbTimeseriesStore(TimeseriesStore):
    """Writes points to QuestDB using ILP over HTTP."""

    def __init__(
        self,
        ilp_http_url: str,
        retry_cfg: Dict[str, int],
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.url = ilp_http_url.rstrip("/")
        self.logger = logger or logging.getLogger(__name__)
        self.max_retries = int(retry_cfg.get("max_retries", 3))
        self.base_backoff_ms = int(retry_cfg.get("base_backoff_ms", 200))
        self.max_backoff_ms = int(retry_cfg.get("max_backoff_ms", 5000))

    def write_points(self, points: list[Point]) -> None:
        if not points:
            return
        payload = "\n".join(point_to_line(p) for p in points).encode("utf-8")
        attempt = 0
        backoff = self.base_backoff_ms / 1000.0
        while True:
            try:
                req = urllib.request.Request(self.url, data=payload, method="POST")
                req.add_header("Content-Type", "text/plain")
                with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
                    if resp.status >= 300:
                        raise RuntimeError(f"QuestDB ILP HTTP status {resp.status}")
                return
            except Exception as exc:  # pylint: disable=broad-except
                attempt += 1
                if attempt > self.max_retries:
                    self.logger.warning("QuestDB write failed after retries: %s", exc)
                    return
                time.sleep(backoff)
                backoff = min(self.max_backoff_ms / 1000.0, backoff * 2)

    def flush(self) -> None:
        return
