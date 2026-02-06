"""Policy contract v1 with runtime validation (stdlib only)."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

POLICY_VERSION = "policy.v1"
ALLOWED_MODES = {"normal", "scalp", "risk_off", "conservative"}


def _require_type(value: Any, expected: tuple[type, ...], name: str) -> None:
    if not isinstance(value, expected):
        raise ValueError(f"{name} must be {expected}, got {type(value).__name__}")


def _require_non_empty_str(value: Any, name: str) -> str:
    _require_type(value, (str,), name)
    if not value.strip():
        raise ValueError(f"{name} must be non-empty")
    return value


def _require_int(value: Any, name: str, minimum: int = 0) -> int:
    _require_type(value, (int,), name)
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _require_float(value: Any, name: str, minimum: float = 0.0) -> float:
    _require_type(value, (int, float), name)
    val = float(value)
    if val < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return val


def _validate_policy_dict(raw: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("policy payload must be a JSON object")

    version = _require_non_empty_str(raw.get("version"), "version")
    ts = _require_int(raw.get("ts"), "ts", minimum=0)
    ttl_sec = _require_int(raw.get("ttl_sec"), "ttl_sec", minimum=1)
    allow_trading = raw.get("allow_trading")
    _require_type(allow_trading, (bool,), "allow_trading")
    mode = _require_non_empty_str(raw.get("mode"), "mode")
    if mode not in ALLOWED_MODES:
        raise ValueError(f"mode must be one of {sorted(ALLOWED_MODES)}")

    size_multiplier = raw.get("size_multiplier", 1.0)
    size_multiplier = _require_float(size_multiplier, "size_multiplier", minimum=0.0)
    cooldown_sec = raw.get("cooldown_sec", 0)
    cooldown_sec = _require_int(cooldown_sec, "cooldown_sec", minimum=0)

    spread_max_bps = raw.get("spread_max_bps")
    if spread_max_bps is not None:
        spread_max_bps = _require_float(spread_max_bps, "spread_max_bps", minimum=0.0)

    max_daily_loss = raw.get("max_daily_loss")
    if max_daily_loss is not None:
        max_daily_loss = _require_float(max_daily_loss, "max_daily_loss", minimum=0.0)

    reason = raw.get("reason")
    if reason is None or not str(reason).strip():
        reason = "OK"
    reason = _require_non_empty_str(str(reason), "reason")

    return {
        "version": version,
        "ts": ts,
        "ttl_sec": ttl_sec,
        "allow_trading": bool(allow_trading),
        "mode": mode,
        "size_multiplier": size_multiplier,
        "cooldown_sec": cooldown_sec,
        "spread_max_bps": spread_max_bps,
        "max_daily_loss": max_daily_loss,
        "reason": reason,
    }


def policy_fingerprint(policy: "Policy") -> str:
    payload = policy.to_json(pretty=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass
class Policy:
    version: str
    ts: int
    ttl_sec: int
    allow_trading: bool
    mode: str
    size_multiplier: float = 1.0
    cooldown_sec: int = 0
    spread_max_bps: Optional[float] = None
    max_daily_loss: Optional[float] = None
    reason: str = "OK"

    def __post_init__(self) -> None:
        _validate_policy_dict(self.to_dict())

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Policy":
        data = _validate_policy_dict(raw)
        return cls(**data)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "ts": int(self.ts),
            "ttl_sec": int(self.ttl_sec),
            "allow_trading": bool(self.allow_trading),
            "mode": self.mode,
            "size_multiplier": float(self.size_multiplier),
            "cooldown_sec": int(self.cooldown_sec),
            "spread_max_bps": self.spread_max_bps,
            "max_daily_loss": self.max_daily_loss,
            "reason": self.reason,
        }

    def to_json(self, pretty: bool = False) -> str:
        payload = self.to_dict()
        if pretty:
            return json.dumps(payload, indent=2, sort_keys=True)
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)

    def is_fresh(self, now_ts: Optional[int] = None, grace_sec: int = 0) -> bool:
        now_ts = int(time.time()) if now_ts is None else int(now_ts)
        grace = max(0, int(grace_sec))
        return now_ts <= int(self.ts) + int(self.ttl_sec) + grace

