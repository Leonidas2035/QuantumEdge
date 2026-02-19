"""
Smart Execution Layer.
Handles Limit Order Chasing and Maker/Taker optimisations.
"""

import asyncio
import logging
from typing import Any, Dict, Optional

# Assuming we can import binance exceptions or similar

logger = logging.getLogger(__name__)


class SmartExecutor:
    """
    Executes orders with 'smart' logic:
    - MEDIUM/LOW urgency: Try Limit at BBO (Best Bid/Ask).
    - Chase price if it moves away.
    - Fallback to Market if not filled after N attempts or time.
    """

    def __init__(self, exchange_client: Any, config: Dict[str, Any] = None):
        self.client = exchange_client
        self.config = config or {}

        # execution config
        exec_cfg = self.config.get("execution", {})
        self.default_urgency = exec_cfg.get("default_urgency", "MEDIUM")
        self.chase_interval = exec_cfg.get("chase_interval_ms", 500) / 1000.0
        self.max_chase_attempts = exec_cfg.get("max_chase_attempts", 3)
        self.taker_fee_bps = exec_cfg.get("taker_fee_bps", 4.0)

    async def execute_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        urgency: str = "MEDIUM",
        reduce_only: bool = False,
    ) -> Dict[str, Any]:
        """
        Execute order with specified urgency.
        Returns the final fill result (or partial).
        """
        symbol = symbol.upper()
        side = side.upper()

        if urgency == "HIGH":
            # Immediate Market
            return await self._execute_market(symbol, side, qty, reduce_only)

        # MEDIUM / LOW -> Try Limit Chase
        return await self._execute_limit_chase(symbol, side, qty, reduce_only, urgency)

    async def _execute_market(
        self, symbol: str, side: str, qty: float, reduce_only: bool
    ) -> Dict[str, Any]:
        logger.info(f"Placing MARKET {side} {symbol} {qty}")
        try:
            # Helper to call client.futures_create_order or create_order depending on client type
            # Assuming client has a unified interface or we check methods
            if hasattr(self.client, "futures_create_order"):
                return await self.client.futures_create_order(
                    symbol=symbol,
                    side=side,
                    type="MARKET",
                    quantity=qty,
                    reduceOnly=reduce_only,
                )
            else:
                return await self.client.create_order(
                    symbol=symbol, side=side, type="MARKET", quantity=qty
                )
        except Exception as e:
            logger.error(f"Market Order Failed: {e}")
            return {}

    async def _execute_limit_chase(
        self, symbol: str, side: str, qty: float, reduce_only: bool, urgency: str
    ) -> Dict[str, Any]:

        remaining_qty = qty
        attempts = 0
        current_order_id = None

        while attempts < self.max_chase_attempts and remaining_qty > 0:
            attempts += 1

            # 1. Get BBO
            price = await self._get_bbo_price(symbol, side)
            if not price:
                logger.warning("Failed to get BBO, falling back to Market")
                break

            # 2. Place Limit (Post-Only if possible/supported, otherwise GTC)
            # For simplicity, GTC Limit at BBO
            logger.info(
                f"Chase {attempts}/{self.max_chase_attempts}: Limit {side} {symbol} {remaining_qty} @ {price}"
            )

            try:
                if hasattr(self.client, "futures_create_order"):
                    order = await self.client.futures_create_order(
                        symbol=symbol,
                        side=side,
                        type="LIMIT",
                        quantity=remaining_qty,
                        price=price,
                        timeInForce="GTC",
                        reduceOnly=reduce_only,
                    )
                else:
                    order = await self.client.create_order(
                        symbol=symbol,
                        side=side,
                        type="LIMIT",
                        quantity=remaining_qty,
                        price=price,
                        timeInForce="GTC",
                    )

                current_order_id = order.get("orderId")

            except Exception as e:
                logger.warning(f"Limit placement failed: {e}. Retrying/Fallback.")
                break

            # 3. Wait
            await asyncio.sleep(self.chase_interval)

            # 4. Check Status
            status_res = await self._get_order_status(symbol, current_order_id)
            if not status_res:
                logger.warning("Order status unknown, aborting chase to be safe")
                return {"status": "UNKNOWN", "orderId": current_order_id}

            status = status_res.get("status")
            executed = float(status_res.get("executedQty", 0.0))
            remaining_qty = (
                qty - executed
            )  # Update remaining from total context if strictly tracking,
            # or status_res might reflect this specific order's fill.
            # Wait, if we cancel & replace, we need to track cumulative fill?
            # Actually, `executed` here is for THIS order.
            # We need to track `filled_total`.

            # Correction: logic should track total filled across attempts or relying on order replacement
            # If partial fill, we only cancel remainder.

            effective_rem = float(status_res.get("origQty", 0.0)) - executed
            # (Assuming origQty matches what we sent)

            if status == "FILLED" or effective_rem <= 0:
                return status_res

            if status == "CANCELED" or status == "REJECTED":
                # Odd, but OK, retry loop
                pass
            elif status == "NEW" or status == "PARTIALLY_FILLED":
                # Check if price moved away
                new_bbo = await self._get_bbo_price(symbol, side)

                # If Price hasn't moved (much), maybe keep waiting?
                # Urgency MEDIUM: We chase quickly.
                # If new_bbo != price -> Cancel and Replace
                # Or just Cancel and next loop checks price

                if new_bbo != price:
                    # Cancel
                    await self._cancel_order(symbol, current_order_id)
                    remaining_qty = effective_rem  # Next loop places for this amount
                else:
                    # Price same. If urgency LOW, maybe wait more?
                    # If MEDIUM, we count attempt. If max attempts reached, we fall through to Market.
                    # To "Force" the loop continuation, we cancel logic here.
                    await self._cancel_order(symbol, current_order_id)
                    remaining_qty = effective_rem

            if remaining_qty <= 0:
                return status_res

        # End of Loop
        if remaining_qty > 0:
            if urgency == "LOW":
                logger.info("Low urgency chase failed, not placing Market.")
                return {"status": "CANCELED", "executedQty": qty - remaining_qty}
            else:
                # Fallback Market
                logger.info(f"Chase ended. Market execution for rem {remaining_qty}")
                return await self._execute_market(
                    symbol, side, remaining_qty, reduce_only
                )

        return {"status": "FILLED"}

    async def _get_bbo_price(self, symbol: str, side: str) -> Optional[float]:
        try:
            # client.get_order_book_ticker or futures_order_book_ticker
            if hasattr(
                self.client, "futures_symbol_ticker"
            ):  # Not ideal, looking for book ticker
                # futures_order_book_ticker not always available on all wrappers?
                # Using orderbook depth 5 might be safer/standard
                pass

            # Generic method
            # Assuming binance client
            ticker = None
            if hasattr(self.client, "futures_order_book_ticker"):
                ticker = await self.client.futures_order_book_ticker(symbol=symbol)
            elif hasattr(self.client, "get_order_book_ticker"):
                ticker = await self.client.get_order_book_ticker(symbol=symbol)

            if ticker:
                # If BUY -> Bid (Maker) or Ask (Taker)?
                # Limit Choice:
                # If we want to be Maker (Post-Only), we place at Best Bid (Buy) or Best Ask (Sell).
                # If we want instant fill (Taker/Limit) we place at Best Ask (Buy) or Best Bid (Sell).

                # Goal: "Capture Maker rebate". Place at Best Bid (Buy).
                if side == "BUY":
                    return float(ticker["bidPrice"])
                else:
                    return float(ticker["askPrice"])
            return None
        except Exception:
            return None

    async def _cancel_order(self, symbol: str, order_id: str) -> bool:
        try:
            if hasattr(self.client, "futures_cancel_order"):
                await self.client.futures_cancel_order(symbol=symbol, orderId=order_id)
            else:
                await self.client.cancel_order(symbol=symbol, orderId=order_id)
            return True
        except Exception:
            return False

    async def _get_order_status(self, symbol: str, order_id: str) -> Optional[Dict]:
        try:
            if hasattr(self.client, "futures_get_order"):
                return await self.client.futures_get_order(
                    symbol=symbol, orderId=order_id
                )
            else:
                return await self.client.get_order(symbol=symbol, orderId=order_id)
        except Exception:
            return None
