"""Client for fetching SupervisorAgent snapshots."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional
from urllib import error, request

from bot.core.config_loader import SupervisorSnapshotsSettings


@dataclass
class SupervisorSnapshot:
    timestamp: Optional[datetime]
    trend: Optional[str]
    trend_confidence: Optional[float]
    market_risk_level: Optional[str]
    behavior_pnl_quality: Optional[str]
    behavior_signal_quality: Optional[str]
    behavior_flags: list[str]
    raw: dict[str, Any]


class SupervisorSnapshotClient:
    """HTTP client for SupervisorAgent snapshot endpoint."""

    def __init__(self, cfg: SupervisorSnapshotsSettings, logger: logging.Logger) -> None:
        self.cfg = cfg
        self.logger = logger

    def _get_json(self, path: str) -> Optional[dict[str, Any]]:
        url = self.cfg.supervisor_url.rstrip("/") + path
        req = request.Request(url, method="GET")
        try:
            with request.urlopen(req, timeout=self.cfg.timeout_ms / 1000.0) as resp:
                body = resp.read().decode("utf-8")
                if not body:
                    return None
                return json.loads(body)
        except (error.URLError, json.JSONDecodeError) as exc:
            self.logger.warning("Supervisor snapshot fetch failed: %s", exc)
            return None

    async def fetch_snapshot(self) -> Optional[SupervisorSnapshot]:
        loop = asyncio.get_running_loop()
        payload = await loop.run_in_executor(None, self._get_json, self.cfg.endpoint)
        if payload is None:
            return None

        ts_raw = payload.get("timestamp")
        timestamp = None
        try:
            if ts_raw:
                timestamp = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        except Exception:
            timestamp = None

        return SupervisorSnapshot(
            timestamp=timestamp,
            trend=payload.get("trend"),
            trend_confidence=payload.get("trend_confidence"),
            market_risk_level=payload.get("market_risk_level"),
            behavior_pnl_quality=payload.get("behavior_pnl_quality"),
            behavior_signal_quality=payload.get("behavior_signal_quality"),
            behavior_flags=list(payload.get("behavior_flags", []) or []),
            raw=payload,
        )
