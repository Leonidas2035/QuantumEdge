"""
PaperTrader — Shadow Mode Execution Gateway.

Replaces BinanceExecutionGateway when running in data-collection mode
(no HTTP requests, no geo-block errors).  Logs all "executions" locally
and maintains a lightweight fill history for auditing.
"""

import logging
import time
import uuid
from decimal import Decimal
from typing import List, Dict, Any, Optional

from quantum_edge_core.ai_scalper_bot.bot.execution.strategy_core import TradeAction

logger = logging.getLogger("PaperTrader")

# ── Hardcoded fallback for paper mode ────────────────────────────────
PAPER_FALLBACK_BALANCE_USDT: float = 10_000.0


class PaperTrader:
    """Drop-in replacement for BinanceExecutionGateway."""

    def __init__(self, config: Optional[Any] = None) -> None:
        self.symbol: str = config.symbol if config else "BTCUSDT"
        self.fills: List[Dict[str, Any]] = []

        # ── Auto-start: paper mode is ALWAYS running ────────────
        self.status: str = "RUNNING"
        self.entries_paused: bool = False

        # ── Paper balance bootstrap ──────────────────────────────
        # After a DB wipe the balance is 0.  For paper mode we
        # inject a hardcoded fallback so grid volume is never zero.
        self.quote_balance: Decimal = Decimal(str(PAPER_FALLBACK_BALANCE_USDT))

        logger.info(
            "PaperTrader initialised (Shadow Mode) — "
            "status=%s, fallback USDT balance: %.2f — no live orders",
            self.status,
            float(self.quote_balance),
        )

    async def execute(self, action: TradeAction) -> bool:
        if self.quote_balance <= 0:
            logger.error("INSUFFICIENT BALANCE DETECTED — forcing fallback $10k")
            self.quote_balance = Decimal(str(PAPER_FALLBACK_BALANCE_USDT))

        if action.qty <= 0:
            logger.error("ZERO QTY — skipping execution")
            return False

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

        # Track quote balance for paper fills
        if side == "BUY":
            self.quote_balance -= Decimal(str(action.price)) * Decimal(str(action.qty))
        else:
            self.quote_balance += Decimal(str(action.price)) * Decimal(str(action.qty))

        logger.info(
            f"PAPER TRADE EXECUTED: {side} {action.qty:.6f} @ {action.price:.2f} | new_balance={float(self.quote_balance):.2f}"
        )
        return True

    async def close(self) -> None:
        logger.info(
            "PaperTrader closed. Total fills: %d",
            len(self.fills),
        )
