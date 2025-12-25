"""SupervisorAgent heartbeat client."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional
from urllib import request, error


@dataclass
class SupervisorClientConfig:
    base_url: str
    api_token: str
    timeout_s: float
    heartbeat_interval_s: float
    on_error: str = "log_and_continue"
    risk_enabled: bool = True
    risk_on_error: str = "bypass"  # "bypass" or "block"
    risk_log_level: str = "info"


class SupervisorClient:
    """Lightweight client for SupervisorAgent HTTP API."""

    def __init__(self, cfg: SupervisorClientConfig, logger: logging.Logger) -> None:
        self.cfg = cfg
        self.logger = logger
        self._last_heartbeat_ts: float = 0.0

    def _post_json(self, path: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        url = self.cfg.base_url.rstrip("/") + path
        headers = {
            "Content-Type": "application/json",
        }
        if self.cfg.api_token:
            headers["X-API-TOKEN"] = self.cfg.api_token

        data = json.dumps(payload).encode("utf-8")
        req = request.Request(url, data=data, headers=headers, method="POST")

        try:
            with request.urlopen(req, timeout=self.cfg.timeout_s) as resp:
                body = resp.read().decode("utf-8")
                if not body:
                    return None
                return json.loads(body)
        except (error.URLError, json.JSONDecodeError) as exc:
            self.logger.warning("Supervisor API request failed (%s): %s", path, exc)
            return None

    async def send_heartbeat_if_due(self, payload_builder: Callable[[], Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        now = time.monotonic()
        if now - self._last_heartbeat_ts < self.cfg.heartbeat_interval_s:
            return None

        payload = payload_builder()
        loop = asyncio.get_running_loop()
        self._last_heartbeat_ts = now
        return await loop.run_in_executor(
            None,
            self._post_json,
            "/api/v1/heartbeat",
            payload,
        )

    async def evaluate_order(self, order_payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Evaluate an order via Supervisor risk gateway."""

        if not self.cfg.risk_enabled:
            return None

        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None,
            self._post_json,
            "/api/v1/risk/evaluate",
            order_payload,
        )

        if response is None:
            if self.cfg.risk_on_error == "block":
                return {
                    "allowed": False,
                    "code": "SUPERVISOR_UNREACHABLE",
                    "reason": "SupervisorAgent unavailable; blocking new orders.",
                }
            return None

        level = self.cfg.risk_log_level.lower()
        msg = f"Supervisor decision: allowed={response.get('allowed')} code={response.get('code')} reason={response.get('reason')}"
        if level == "debug":
            self.logger.debug(msg)
        elif level == "warning":
            self.logger.warning(msg)
        else:
            self.logger.info(msg)

        return response
