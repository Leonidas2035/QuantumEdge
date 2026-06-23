"""
Smart Execution Layer for AI Scalper Bot.
Decouples order side from position side and forwards requests to CCXT.
"""

from enum import StrEnum
from dataclasses import dataclass
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class PositionSide(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"
    BOTH = "BOTH"  # Compliance for One-Way mode if required


@dataclass(slots=True)
class OrderRequest:
    symbol: str
    side: OrderSide
    position_side: PositionSide
    qty: float
    price: float | None = None  # None for market orders
    client_oid: str | None = None


class SmartExecutor:
    """
    Executes trades securely on behalf of the bot, enforcing strict Hedge Mode parameter rules.
    """

    def __init__(self, exchange_client: Any, config: Dict[str, Any] = None) -> None:
        self.exchange_client = exchange_client
        self.config = config or {}

    async def place_order(self, request: OrderRequest) -> dict:
        """
        Sends an OrderRequest to the exchange client, properly setting the positionSide parameters.
        """
        if request.qty <= 0:
            raise ValueError(f"Invalid order quantity: {request.qty}")

        params = {
            "positionSide": request.position_side.value
        }

        if request.client_oid:
            params["clientOrderId"] = request.client_oid

        try:
            logger.info(
                f"Submitting order to exchange | {request.symbol} | "
                f"Action: {request.side.value} | Target Position: {request.position_side.value} | Qty: {request.qty}"
            )

            # Unified CCXT create_order syntax
            response = await self.exchange_client.create_order(
                symbol=request.symbol,
                type="limit" if request.price is not None else "market",
                side=request.side.value.lower(),
                amount=request.qty,
                price=request.price,
                params=params,
            )
            return response
        except Exception as e:
            logger.error(f"Execution failed for {request.client_oid}: {str(e)}", exc_info=True)
            raise e
