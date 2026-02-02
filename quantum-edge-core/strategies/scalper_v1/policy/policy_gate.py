"""Policy gating helpers for runtime decisions."""

from __future__ import annotations

import time
from typing import Optional

from .policy_contract import Policy


def policy_allows_entry(action: str, policy: Policy, now_ts: Optional[float] = None) -> bool:
    action_name = str(action).lower()
    if action_name != "enter":
        return True
    if not policy.allow_trading or policy.mode == "risk_off":
        return False
    now = float(now_ts if now_ts is not None else time.time())
    if policy.cooldown_sec > 0 and now < (policy.ts + policy.cooldown_sec):
        return False
    return True
