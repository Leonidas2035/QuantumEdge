"""Security helpers for dashboard controls."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional


def dashboard_auth_required(mode: str) -> bool:
    return mode.lower() == "token"


def check_dashboard_auth(headers: Dict[str, str], mode: str, token: str) -> bool:
    if not dashboard_auth_required(mode):
        return True
    auth = headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return False
    provided = auth.replace("Bearer ", "", 1).strip()
    return bool(token) and provided == token


def is_path_allowed(candidate: Path, base: Path) -> bool:
    try:
        candidate = candidate.resolve()
        base = base.resolve()
        return candidate.is_relative_to(base)
    except Exception:
        return False


def dashboard_auth_mode() -> str:
    return str(os.getenv("DASHBOARD_AUTH", "none")).lower()


def dashboard_auth_token() -> Optional[str]:
    return os.getenv("DASHBOARD_TOKEN")


def validate_kill_switch_challenge(
    challenge: Optional[Dict[str, Any]], challenge_id: str, now: float
) -> Optional[str]:
    if not challenge or challenge.get("challenge_id") != challenge_id:
        return "challenge_mismatch"
    expires_at = challenge.get("expires_at", 0)
    try:
        expires = float(expires_at)
    except (TypeError, ValueError):
        expires = 0.0
    if now > expires:
        return "challenge_expired"
    return None
