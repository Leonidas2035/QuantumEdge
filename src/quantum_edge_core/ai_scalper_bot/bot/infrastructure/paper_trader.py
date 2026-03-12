"""
PaperTrader — Shadow Mode Execution Gateway.

Replaces BinanceExecutionGateway when running in data-collection mode
(no HTTP requests, no geo-block errors).  Logs all "executions" locally
and maintains a lightweight fill history for auditing.
"""

import logging
import time
import uuid
from typing import List, Dict, Any

from quantum_edge_core.ai_scalper_bot.bot.execution.strategy_core import TradeAction

logger = logging.getLogger("PaperTrader")


class PaperTrader:
    """Drop-in replacement for BinanceExecutionGateway."""

    def __init__(self, config=None):
        self.symbol = config.symbol if config else "BTCUSDT"
        self.fills: List[Dict[str, Any]] = []
        logger.info("PaperTrader initialised (Shadow Mode) — no live orders")

    async def execute(self, action: TradeAction) -> bool:
        if action.action_type == "CANCEL_ALL":
            logger.info(f"✅ PAPER TRADE: Canceled all open orders | {action.reason}")
            return True

        if action.action_type == "SYNC_GRID":
            logger.info(
                f"✅ PAPER TRADE: Grid Synced around {action.price} | {action.reason}"
            )
            # In a full paper trader, we would maintain an internal virtual order book
            # and simulate fills based on tick data. For now we just log the sync.
            return True

        side = "BUY" if "BUY" in action.action_type else "SELL"
        fill_id = str(uuid.uuid4())[:8]
        fill = {
            "id": fill_id,
            "symbol": self.symbol,
            "side": side,
            "qty": action.qty,
            "price": action.price,
            "reason": action.reason,
            "ts": time.time(),
        }
        self.fills.append(fill)

        logger.info(
            "✅ PAPER TRADE EXECUTED: %s %.4f %s @ %.2f [%s] (id=%s)",
            side,
            action.qty,
            self.symbol,
            action.price,
            action.reason,
            fill_id,
        )
        return True

    async def close(self):
        logger.info(
            "PaperTrader closed. Total fills: %d",
            len(self.fills),
        )
