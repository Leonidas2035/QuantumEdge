"""QuestDB ILP store."""

from __future__ import annotations

import logging
import time
import urllib.request
from typing import Dict, Optional

from quantum_edge_core.supervisor.supervisor.tsdb.base import Point, TimeseriesStore


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
    escaped = str(value).replace(chr(34), r"\"")
    return f'"{escaped}"'


import math

def point_to_line(point: Point) -> str:
    tags = ",".join(f"{_escape(k)}={_escape(v)}" for k, v in point.tags.items())
    valid_fields = {}
    for k, v in point.fields.items():
        if isinstance(v, float) and math.isnan(v):
            continue
        if isinstance(v, str) and v.lower() in ("nan", "infinity", "-infinity"):
            continue
        if v is None:
            continue
        valid_fields[k] = v

    if not valid_fields:
        return ""

    fields = ",".join(
        f"{_escape(k)}={_encode_field(v)}" for k, v in valid_fields.items()
    )
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
        lines = [point_to_line(p) for p in points]
        lines = [L for L in lines if L]  # filter skipped points
        if not lines:
            return
        payload = "\n".join(lines).encode("utf-8")
        
        # Debug requested by user
        if self.logger:
            try:
                self.logger.error(f"DEBUG QUESTDB PAYLOAD:\n{payload.decode('utf-8')}")
            except Exception:
                pass

        attempt = 0
        backoff = self.base_backoff_ms / 1000.0
        while True:
            try:
                req = urllib.request.Request(self.url, data=payload, method="POST")
                req.add_header("Content-Type", "text/plain")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    if resp.status >= 300:
                        raise RuntimeError(f"QuestDB ILP HTTP status {resp.status}")
                return
            except Exception as exc:  # pylint: disable=broad-except
                if hasattr(exc, "code") and exc.code == 400 and hasattr(exc, "read"):
                    try:
                        resp_text = exc.read().decode("utf-8", errors="replace")
                        self.logger.error(f"QUESTDB 400 ERROR! Payload that caused it:\n{payload.decode('utf-8')}")
                        self.logger.error(f"Response text: {resp_text}")
                    except Exception:
                        pass
                attempt += 1
                if attempt > self.max_retries:
                    self.logger.warning("QuestDB write failed after retries: %s", exc)
                    return
                time.sleep(backoff)
                backoff = min(self.max_backoff_ms / 1000.0, backoff * 2)

    def flush(self) -> None:
        return
