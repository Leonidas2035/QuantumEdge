"""Order policy helper for scalp-mode execution.

This module does not introduce real limit-order book handling yet. Instead it
encapsulates how we *would* place orders and keeps logging transparent. The
actual executor still uses the existing trader.process API to avoid breaking
current flows.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional


class OrderPolicy:
    """Encapsulate scalp order preferences (limit vs market, offsets, cancels)."""

    def __init__(self, settings: Dict[str, Any], logger: Optional[logging.Logger] = None):
        self.settings = settings or {}
        self.logger = logger or logging.getLogger(__name__)

        policy = self.settings
        self.prefer_limit = bool(policy.get("prefer_limit", True))
        self.post_only = bool(policy.get("post_only", False))
        self.near_touch_offset_bps = float(policy.get("near_touch_offset_bps", 1.0))
        self.cancel_timeout_ms = int(policy.get("cancel_timeout_ms", 1500))
        self.max_partial_fill_time_ms = int(policy.get("max_partial_fill_time_ms", 2000))
        self.min_fill_ratio_before_cancel = float(policy.get("min_fill_ratio_before_cancel", 0.25))

    async def place_scalp_order(
        self,
        trader,
        side: str,
        size: float,
        price: float,
        timestamp: int,
        symbol: str,
        tp_price: float = None,
        sl_price: float = None,
    ) -> Dict[str, Any]:
        """
        Place a scalp entry/exit.

        For now we map to the existing trader.process call (MARKET-equivalent)
        while keeping placeholders for limit/partial-fill handling.
        """
        order_type = "limit" if self.prefer_limit else "market"
        if self.prefer_limit:
            # We don't have orderbook details; just log intent.
            self.logger.debug(
                "Placing near-touch %s limit (post_only=%s offset_bps=%.3f) for %s size=%.4f",
                side,
                self.post_only,
                self.near_touch_offset_bps,
                symbol,
                size,
            )
        decision_obj = type(
            "TmpDecision",
            (),
            {
                "action": side,
                "size": size,
                "order_type": order_type,
                "tp_price": tp_price,
                "sl_price": sl_price,
            },
        )
        await trader.process(decision_obj, price, timestamp, symbol=symbol)
        return {"filled": True, "order_type": order_type, "size": size}

    async def close_position(self, trader, size: float, price: float, timestamp: int, symbol: str) -> Dict[str, Any]:
        """Close an open position using the existing executor API."""
        decision_obj = type(
            "TmpDecision",
            (),
            {"action": "close", "size": size, "order_type": "market"},
        )
        await trader.process(decision_obj, price, timestamp, symbol=symbol)
        return {"closed": True, "size": size}
