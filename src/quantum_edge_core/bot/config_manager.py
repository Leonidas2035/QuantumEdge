"""
Dynamic Configuration Manager for Trading Bot.
Handles runtime updates to risk parameters and operating modes.
"""

from __future__ import annotations
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)


class DynamicConfig:
    """
    Manages the Bot's dynamic configuration state.
    """

    def __init__(self):
        self.current_mode = "NORMAL"  # NORMAL, FREEZE, REDUCE_ONLY, SNIPER_ONLY
        self.overrides: Dict[str, float] = {}

        # Defaults (would typically come from initial config)
        self.defaults = {"leverage_cap": 20.0, "min_order_size": 10.0, "dca_multiplier": 1.0, "min_confidence": 0.65}

    def apply_policy(self, policy: Dict[str, Any]):
        """
        Apply a new policy from the Supervisor.
        """
        action = policy.get("action")
        # Map ACtion to Mode (simplified mapping)

        # Logic:
        # CLOSE_ALL -> Special trigger handled by service, likely enters FREEZE or RESTART
        # FREEZE -> FREEZE
        # REDUCE_SIZE -> REDUCE_ONLY (or just reduce leverage)
        # CONTINUE -> NORMAL (or keep previous if compatible)

        # For this prototype, we map explicitly or use logic
        if action == "FREEZE":
            self.current_mode = "FREEZE"
        elif action == "CLOSE_ALL":
            self.current_mode = "FREEZE"  # And trigger close
        elif action == "REDUCE_SIZE":
            self.current_mode = "REDUCE_ONLY"
        elif action == "CONTINUE":
            self.current_mode = "NORMAL"

        # Params Override
        params = policy.get("params_override", {})
        if params:
            for k, v in params.items():
                if k in self.defaults or k in ["leverage_cap", "min_order_size"]:  # Allow specific known keys
                    try:
                        val_f = float(v)
                        self.overrides[k] = val_f
                        logger.info(f"Dynamic Config Update: {k} = {val_f}")
                    except (ValueError, TypeError):
                        logger.warning(f"Invalid param value for {k}: {v}")

    def get_param(self, key: str) -> float:
        """Get parameter value (Override > Default)."""
        return self.overrides.get(key, self.defaults.get(key, 0.0))

    def get_mode(self) -> str:
        return self.current_mode
