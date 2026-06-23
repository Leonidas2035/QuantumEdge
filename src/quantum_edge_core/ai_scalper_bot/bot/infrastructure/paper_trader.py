"""
PaperTrader — Shadow Mode Execution Gateway.

Replaces BinanceExecutionGateway when running in data-collection mode
(no HTTP requests, no geo-block errors).  Logs all "executions" locally
and maintains a lightweight fill history for auditing.

Supports:
- Single TradeAction execution (legacy SCALP)
- List[TradeAction] execution (DCA Grid batch)
- SYNC_GRID expansion into individual LIMIT orders
"""

import os
import logging
import time
import uuid
from decimal import Decimal
from typing import List, Dict, Any, Optional, Union

from quantum_edge_core.ai_scalper_bot.bot.execution.strategy_core import TradeAction

logger = logging.getLogger("PaperTrader")

# ── Hardcoded fallback for paper mode ────────────────────────────────
PAPER_FALLBACK_BALANCE_USDT: float = float(os.getenv("PAPER_TRADER_START_BALANCE", "10000.0"))


class PaperTrader:
    """Drop-in replacement for BinanceExecutionGateway."""

    def __init__(self, config: Optional[Any] = None) -> None:
        import os

        if os.getenv("EXECUTION_MODE", "").lower() == "live":
            logger.error(
                "PAPERTRADER STILL ACTIVE IN LIVE MODE! This should never happen!"
            )

        self.symbol: str = config.symbol if config else "BTCUSDT"
        self.fills: List[Dict[str, Any]] = []
        self.open_orders: List[Dict[str, Any]] = []

        # ── Auto-start: paper mode is ALWAYS running ────────────
        self.status: str = "RUNNING"
        self.entries_paused: bool = False

        # ── Paper balance bootstrap ──────────────────────────────
        self.quote_balance: Decimal = Decimal(str(PAPER_FALLBACK_BALANCE_USDT))

        # Grid config (set from strategy on SYNC_GRID)
        self.grid_levels_below: int = 15
        self.grid_levels_above: int = 15

        logger.info(
            "PaperTrader initialised (Shadow Mode) — "
            "status=%s, fallback USDT balance: %.2f — no live orders",
            self.status,
            float(self.quote_balance),
        )

    async def execute(self, action: Union[TradeAction, Any, List[TradeAction]]) -> bool:
        """Execute a single action or a batch of grid orders."""
        # ── Handle list of orders (DCA Grid batch) ───────────────
        if isinstance(action, list):
            placed = 0
            for order in action:
                result = await self._execute_single(order)
                if result:
                    placed += 1
            logger.warning(
                "!!! GRID BATCH COMPLETE: %d/%d orders placed !!!",
                placed,
                len(action),
            )
            return placed > 0

        # ── Single action ────────────────────────────────────────
        return await self._execute_single(action)

    async def _execute_single(self, action: Any) -> bool:
        """Process a single TradeAction or OrderRequest."""
        if self.quote_balance <= 0:
            logger.error("INSUFFICIENT BALANCE DETECTED — forcing fallback $10k")
            self.quote_balance = Decimal(str(PAPER_FALLBACK_BALANCE_USDT))

        if hasattr(action, "action_type") and action.action_type == "CANCEL_ALL":
            cancelled = len(self.open_orders)
            self.open_orders.clear()
            logger.info(
                "✅ PAPER TRADE: Canceled %d open orders | %s",
                cancelled,
                action.reason,
            )
            return True

        if hasattr(action, "action_type") and action.action_type == "SYNC_GRID":
            return await self._expand_sync_grid(action)

        if hasattr(action, "action_type") and action.action_type == "ORDER_FILLED":
            return await self._order_filled_counter(action)

        # ── Direct BUY/SELL order ────────────────────────────────
        return self._place_limit_order(action)

    async def _order_filled_counter(self, action: TradeAction) -> bool:
        """
        Counter-order logic: when BUY fills -> place SELL at +spacing, when SELL fills -> place BUY at -spacing.
        Expects reason to contain: side=BUY|spacing_pct=X
        """
        try:
            params = dict(item.split("=") for item in action.reason.split("|"))
            spacing_pct = float(params.get("spacing_pct", 0.012))
            filled_side = params.get("side", "BUY").upper()
        except Exception:
            spacing_pct = 0.012
            filled_side = "BUY"

        orig_price = float(action.price)
        amount = float(action.qty)

        if filled_side == "BUY":
            new_side = "SELL"
            new_price = round(orig_price * (1 + spacing_pct), 2)
            pos_side = "LONG"
        else:
            new_side = "BUY"
            new_price = round(orig_price * (1 - spacing_pct), 2)
            pos_side = "SHORT"

        logger.warning(
            f"✅ PAPER: Executing Counter-Order -> {new_side} @ {new_price} (positionSide={pos_side})"
        )

        order = TradeAction(
            action_type=f"GRID_{new_side}_{pos_side}",
            price=Decimal(str(new_price)),
            qty=Decimal(str(amount)),
            reason=f"Counter-Order for {filled_side} @ {orig_price}",
        )
        return self._place_limit_order(order)

    async def _expand_sync_grid(self, action: TradeAction) -> bool:
        """Expand a SYNC_GRID action into individual LIMIT orders.

        Generates grid_levels_below BUY orders below current price
        and grid_levels_above SELL orders above current price, each
        with the qty from the action.
        """
        center_price = Decimal(str(action.price))
        qty = Decimal(str(action.qty))

        if qty <= 0 or qty < Decimal("0.0001"):
            logger.error(
                "SYNC_GRID REJECTED: qty=%.6f is below minimum!",
                float(qty),
            )
            return False

        # Clear previous grid
        self.open_orders.clear()

        # Parse grid spacing from reason if available, else use 0.2%
        spacing_pct = Decimal("0.002")

        placed = 0
        # Generate BUY orders below center price
        for i in range(1, self.grid_levels_below + 1):
            buy_price = center_price * (Decimal("1.0") - spacing_pct * Decimal(str(i)))
            buy_price = buy_price.quantize(Decimal("0.01"))
            order = TradeAction(
                action_type="BUY",
                price=buy_price,
                qty=qty,
                reason=f"GRID_BUY_L{i}",
            )
            if self._place_limit_order(order):
                placed += 1

        # Generate SELL orders above center price
        for i in range(1, self.grid_levels_above + 1):
            sell_price = center_price * (Decimal("1.0") + spacing_pct * Decimal(str(i)))
            sell_price = sell_price.quantize(Decimal("0.01"))
            order = TradeAction(
                action_type="SELL",
                price=sell_price,
                qty=qty,
                reason=f"GRID_SELL_L{i}",
            )
            if self._place_limit_order(order):
                placed += 1

        logger.warning(
            "!!! SYNC_GRID EXPANDED: %d LIMIT orders placed | "
            "center=%.2f | qty=%.6f BTC | spacing=%.4f%% | %s !!!",
            placed,
            float(center_price),
            float(qty),
            float(spacing_pct * 100),
            action.reason,
        )
        return placed > 0

    def on_tick(self, price: Decimal) -> List[Any]:
        """
        Match open orders against the current price tick.
        Returns a list of filled orders.
        """
        filled_orders = []
        still_open = []
        for order in self.open_orders:
            order_price = Decimal(str(order["price"]))
            side = order["side"]
            qty = Decimal(str(order["qty"]))
            if side == "BUY":
                if price <= order_price:
                    order["status"] = "FILLED"
                    self.fills.append(order)
                    self.quote_balance -= order_price * qty
                    filled_orders.append(order)
                    logger.warning(
                        f"🎯 PAPER ORDER FILLED: BUY {qty} @ {order_price} | Current price: {price} | Bal: {self.quote_balance}"
                    )
                else:
                    still_open.append(order)
            else:  # SELL
                if price >= order_price:
                    order["status"] = "FILLED"
                    self.fills.append(order)
                    self.quote_balance += order_price * qty
                    filled_orders.append(order)
                    logger.warning(
                        f"🎯 PAPER ORDER FILLED: SELL {qty} @ {order_price} | Current price: {price} | Bal: {self.quote_balance}"
                    )
                else:
                    still_open.append(order)
        self.open_orders = still_open
        return filled_orders

    def _place_limit_order(self, action: Any) -> bool:
        """Place a single LIMIT order (BUY or SELL)."""
        from quantum_edge_core.ai_scalper_bot.bot.execution.smart_executor import OrderRequest

        if isinstance(action, OrderRequest):
            qty = Decimal(str(action.qty))
            price = Decimal(str(action.price)) if action.price is not None else Decimal("0.0")
            side = action.side.value.upper()
            pos_side = action.position_side.value.upper()
            reason = f"OrderRequest {side} on {pos_side}"
            client_oid = action.client_oid
        else:
            qty = Decimal(str(action.qty))
            price = Decimal(str(action.price))
            if "BUY" in action.action_type:
                side = "BUY"
            elif "SELL" in action.action_type:
                side = "SELL"
            else:
                side = "BUY" if "BUY" in action.reason.upper() else "SELL"

            if "SHORT" in action.action_type:
                pos_side = "SHORT"
            elif "LONG" in action.action_type:
                pos_side = "LONG"
            else:
                pos_side = "LONG" if side == "BUY" else "SHORT"
            reason = action.reason
            client_oid = None

        if qty <= 0 or qty < Decimal("0.0001"):
            logger.error(
                "ORDER SKIPPED: qty=%.6f is below minimum!",
                float(qty),
            )
            return False

        if price <= 0:
            logger.error("ORDER SKIPPED: price=%.2f is invalid!", float(price))
            return False

        fill_id = client_oid or str(uuid.uuid4())[:8]

        order = {
            "id": fill_id,
            "symbol": self.symbol,
            "side": side,
            "positionSide": pos_side,
            "qty": float(qty),
            "price": float(price),
            "reason": reason,
            "status": "OPEN",
            "ts": time.time(),
        }
        self.open_orders.append(order)

        logger.info(
            "ORDER PLACED (OPEN): %s (%s) %.6f %s @ %.2f | reason=%s (id=%s)",
            side,
            pos_side,
            float(qty),
            self.symbol,
            float(price),
            reason,
            fill_id,
        )
        return True

    async def close(self) -> None:
        logger.info(
            "PaperTrader closed. Total fills: %d, open orders: %d",
            len(self.fills),
            len(self.open_orders),
        )
