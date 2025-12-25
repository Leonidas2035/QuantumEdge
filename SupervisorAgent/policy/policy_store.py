"""Policy store for the SupervisorAgent."""

from __future__ import annotations

import time
from typing import Optional

from .policy_contract import Policy, POLICY_VERSION


class PolicyStore:
    """Simple policy source (static defaults for Stage 0)."""

    def __init__(
        self,
        ttl_sec: int = 30,
        allow_trading: bool = True,
        mode: str = "normal",
        size_multiplier: float = 1.0,
        cooldown_sec: int = 0,
        spread_max_bps: Optional[float] = None,
        max_daily_loss: Optional[float] = None,
        reason: str = "OK",
    ) -> None:
        self.ttl_sec = int(ttl_sec)
        self.allow_trading = bool(allow_trading)
        self.mode = mode
        self.size_multiplier = float(size_multiplier)
        self.cooldown_sec = int(cooldown_sec)
        self.spread_max_bps = spread_max_bps
        self.max_daily_loss = max_daily_loss
        self.reason = reason

    def get_current_policy(self) -> Policy:
        return Policy(
            version=POLICY_VERSION,
            ts=int(time.time()),
            ttl_sec=self.ttl_sec,
            allow_trading=self.allow_trading,
            mode=self.mode,
            size_multiplier=self.size_multiplier,
            cooldown_sec=self.cooldown_sec,
            spread_max_bps=self.spread_max_bps,
            max_daily_loss=self.max_daily_loss,
            reason=self.reason or "OK",
        )

