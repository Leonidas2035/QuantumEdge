"""Policy client for the bot (file/API + TTL enforcement)."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .policy_contract import Policy, policy_fingerprint, POLICY_VERSION


class PolicyClient:
    def __init__(
        self,
        source: str,
        file_path: Path,
        api_url: str,
        ttl_grace_sec: int = 0,
        safe_mode_default: str = "risk_off",
        request_timeout_s: float = 0.3,
        refresh_interval_s: float = 1.0,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.source = source
        self.file_path = file_path
        self.api_url = api_url
        self.ttl_grace_sec = int(ttl_grace_sec or 0)
        self.safe_mode_default = safe_mode_default or "risk_off"
        self.request_timeout_s = float(request_timeout_s)
        self.refresh_interval_s = float(refresh_interval_s)
        self.logger = logger or logging.getLogger("policy_client")

        self._last_policy: Optional[Policy] = None
        self._last_check_ts: float = 0.0
        self._last_fingerprint: Optional[str] = None
        self._last_safe_log_ts: float = 0.0

    def _safe_policy(self, reason: str) -> Policy:
        now_ts = int(time.time())
        return Policy(
            version=POLICY_VERSION,
            ts=now_ts,
            ttl_sec=5,
            allow_trading=False,
            mode=self.safe_mode_default,
            size_multiplier=1.0,
            cooldown_sec=0,
            reason=reason,
        )

    def _is_fresh(self, policy: Policy) -> bool:
        return policy.is_fresh(now_ts=int(time.time()), grace_sec=self.ttl_grace_sec)

    def _log_policy_change(self, policy: Policy) -> None:
        fingerprint = policy_fingerprint(policy)
        if fingerprint == self._last_fingerprint:
            return
        self._last_fingerprint = fingerprint
        self.logger.info(
            "Policy updated: mode=%s allow_trading=%s ttl=%s size_multiplier=%.3f reason=%s hash=%s",
            policy.mode,
            policy.allow_trading,
            policy.ttl_sec,
            policy.size_multiplier,
            policy.reason,
            fingerprint[:12],
        )

    def _log_safe_mode(self, reason: str, cooldown_s: int = 30) -> None:
        now = time.time()
        if now - self._last_safe_log_ts < cooldown_s:
            return
        self._last_safe_log_ts = now
        self.logger.warning("Policy safe mode active: %s", reason)

    def load_from_file(self) -> Optional[Policy]:
        if not self.file_path.exists():
            return None
        try:
            raw = json.loads(self.file_path.read_text(encoding="utf-8"))
            policy = Policy.from_dict(raw)
            if not self._is_fresh(policy):
                return None
            return policy
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("Policy file invalid: %s", exc)
            return None

    def load_from_api(self) -> Optional[Policy]:
        if not self.api_url:
            return None
        req = Request(self.api_url, method="GET")
        try:
            with urlopen(req, timeout=self.request_timeout_s) as resp:
                if resp.status < 200 or resp.status >= 300:
                    return None
                payload = resp.read()
        except (HTTPError, URLError, TimeoutError) as exc:
            self.logger.debug("Policy API unavailable: %s", exc)
            return None
        try:
            raw = json.loads(payload.decode("utf-8")) if payload else {}
            policy = Policy.from_dict(raw)
            if not self._is_fresh(policy):
                return None
            return policy
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("Policy API invalid: %s", exc)
            return None

    def _load_policy(self) -> Optional[Policy]:
        source = (self.source or "auto").lower()
        if source == "file":
            return self.load_from_file()
        if source == "api":
            return self.load_from_api()
        policy = self.load_from_file()
        if policy:
            return policy
        return self.load_from_api()

    def get_policy(self) -> Optional[Policy]:
        now = time.time()
        if self._last_policy and self._is_fresh(self._last_policy):
            return self._last_policy
        if now - self._last_check_ts < self.refresh_interval_s and self._last_policy:
            return self._last_policy
        self._last_check_ts = now
        policy = self._load_policy()
        if policy:
            self._last_policy = policy
            self._log_policy_change(policy)
        return policy

    def get_effective_policy(self) -> Policy:
        policy = self.get_policy()
        if policy:
            return policy
        reason = "POLICY_MISSING_OR_EXPIRED"
        self._log_safe_mode(reason)
        return self._safe_policy(reason)

