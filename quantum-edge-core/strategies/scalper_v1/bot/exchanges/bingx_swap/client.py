import hashlib
import hmac
import logging
import random
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import requests


def _format_number(value: float) -> str:
    return f"{value:.16f}".rstrip("0").rstrip(".")


def _format_param(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return _format_number(value)
    return str(value)


def build_query_string(params: Dict[str, Any]) -> str:
    items = []
    for key in sorted(params.keys()):
        value = params[key]
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            for entry in value:
                items.append((key, _format_param(entry)))
        else:
            items.append((key, _format_param(value)))
    return urlencode(items, doseq=True)


def sign_query(query: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()


@dataclass
class BingXErrorDetail:
    status_code: Optional[int]
    error_code: Optional[int]
    message: str
    endpoint: str


class BingXAPIError(RuntimeError):
    def __init__(self, detail: BingXErrorDetail) -> None:
        self.detail = detail
        super().__init__(detail.message)


class BingXClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        api_secret: str,
        recv_window: int = 5000,
        timeout: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or ""
        self.api_secret = api_secret or ""
        self.recv_window = int(recv_window or 5000)
        self.timeout = float(timeout or 10.0)
        self._min_interval_s = 0.12
        self._next_allowed_ts = 0.0
        self._lock = threading.Lock()
        self.logger = logging.getLogger(__name__)

    def _throttle(self) -> None:
        if self._min_interval_s <= 0:
            return
        with self._lock:
            now = time.monotonic()
            if now < self._next_allowed_ts:
                time.sleep(self._next_allowed_ts - now)
            self._next_allowed_ts = time.monotonic() + self._min_interval_s

    def _build_signed_url(self, path: str, params: Dict[str, Any]) -> str:
        params = dict(params)
        params["timestamp"] = int(time.time() * 1000)
        params["recvWindow"] = self.recv_window
        query = build_query_string(params)
        signature = sign_query(query, self.api_secret)
        return f"{self.base_url}{path}?{query}&signature={signature}"

    def _build_url(self, path: str, params: Optional[Dict[str, Any]]) -> str:
        if not params:
            return f"{self.base_url}{path}"
        query = build_query_string(params)
        return f"{self.base_url}{path}?{query}"

    def _handle_response(self, response: requests.Response, endpoint: str) -> Any:
        status = response.status_code
        try:
            payload = response.json()
        except ValueError:
            detail = BingXErrorDetail(status, None, f"Non-JSON response (status={status})", endpoint)
            self.logger.warning("BingX API error endpoint=%s status=%s code=%s msg=%s", endpoint, status, None, detail.message)
            raise BingXAPIError(detail) from None

        if status != 200:
            error_code = payload.get("code") if isinstance(payload, dict) else None
            message = payload.get("msg") if isinstance(payload, dict) else str(payload)
            detail = BingXErrorDetail(status, error_code, message, endpoint)
            self.logger.warning("BingX API error endpoint=%s status=%s code=%s msg=%s", endpoint, status, error_code, message)
            raise BingXAPIError(detail)

        if isinstance(payload, dict) and payload.get("code") not in (0, None):
            error_code = payload.get("code")
            message = payload.get("msg", "BingX API error")
            detail = BingXErrorDetail(status, error_code, message, endpoint)
            self.logger.warning("BingX API error endpoint=%s status=%s code=%s msg=%s", endpoint, status, error_code, message)
            raise BingXAPIError(detail)

        if isinstance(payload, dict) and "data" in payload:
            return payload.get("data")
        return payload

    def request(self, method: str, path: str, params: Optional[Dict[str, Any]] = None, signed: bool = False) -> Any:
        if signed and not self.api_secret:
            detail = BingXErrorDetail(None, None, "Missing API secret for signed request.", path)
            raise BingXAPIError(detail)

        headers = {}
        if self.api_key:
            headers["X-BX-APIKEY"] = self.api_key

        params = params or {}
        url = self._build_signed_url(path, params) if signed else self._build_url(path, params)

        max_attempts = 3
        backoff = 0.5
        for attempt in range(max_attempts):
            self._throttle()
            try:
                response = requests.request(method, url, headers=headers, timeout=self.timeout)
            except requests.RequestException as exc:
                if attempt < max_attempts - 1:
                    time.sleep(backoff + random.random() * 0.1)
                    backoff = min(backoff * 2, 4.0)
                    continue
                detail = BingXErrorDetail(None, None, f"Request failed: {exc}", path)
                raise BingXAPIError(detail) from exc

            if response.status_code == 429 or response.status_code >= 500:
                if attempt < max_attempts - 1:
                    time.sleep(backoff + random.random() * 0.1)
                    backoff = min(backoff * 2, 4.0)
                    continue
            return self._handle_response(response, path)

        detail = BingXErrorDetail(None, None, "Request failed after retries.", path)
        raise BingXAPIError(detail)
