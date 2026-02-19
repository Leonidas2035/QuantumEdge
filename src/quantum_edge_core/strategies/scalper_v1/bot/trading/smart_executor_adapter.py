"""Adapter that maps bot traders to SmartMakerExecutor ExecutionClient."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Optional

from quantumedge.execution.policies import OrderSide
from quantumedge.execution.types import (ExecutionClient, OrderAck,
                                         OrderPlacement)

# Forward reference for Market
Market = Any


class TraderExecutionAdapter(ExecutionClient):
    def __init__(
        self, trader, market: Market, logger: Optional[logging.Logger] = None
    ) -> None:
        self._trader = trader
        self._market = market
        self._logger = logger or logging.getLogger(__name__)

    async def place_order(self, placement: OrderPlacement) -> OrderAck:
        if hasattr(self._trader, "submit_order"):
            return await self._place_with_submit(placement)
        if hasattr(self._trader, "process"):
            return await self._place_with_process(placement)
        raise RuntimeError("Trader does not support order placement")

    async def cancel_order(
        self,
        *,
        symbol: str,
        order_id: Optional[str] = None,
        client_order_id: Optional[str] = None,
    ) -> bool:
        if hasattr(self._trader, "cancel_order"):
            return await self._trader.cancel_order(
                symbol, order_id=order_id, client_order_id=client_order_id
            )
        return True

    async def _place_with_submit(self, placement: OrderPlacement) -> OrderAck:
        result = await self._trader.submit_order(
            symbol=placement.symbol,
            side="BUY" if placement.side == OrderSide.BUY else "SELL",
            qty=float(placement.quantity),
            reduce_only=placement.reduce_only,
            price=float(placement.price) if placement.price is not None else None,
            client_order_id=placement.client_order_id,
            order_type=placement.order_type,
            time_in_force=placement.time_in_force,
            post_only=placement.post_only,
        )
        if not isinstance(result, dict):
            return OrderAck(order_id=None, client_order_id=placement.client_order_id)
        order_id = result.get("orderId")
        client_oid = result.get("clientOrderId") or placement.client_order_id
        status = result.get("status")
        filled = _as_decimal(result.get("executedQty"))
        avg_price = _as_decimal(result.get("avgPrice") or result.get("price"))
        return OrderAck(
            order_id=str(order_id) if order_id is not None else None,
            client_order_id=client_oid,
            status=status,
            filled_qty=filled,
            avg_price=avg_price,
        )

    async def _place_with_process(self, placement: OrderPlacement) -> OrderAck:
        side = "buy" if placement.side == OrderSide.BUY else "sell"
        decision = type(
            "TmpDecision",
            (),
            {
                "action": side,
                "size": float(placement.quantity),
                "order_type": placement.order_type.lower(),
            },
        )
        await self._trader.process(
            decision, float(placement.price or 0.0), (0), symbol=placement.symbol
        )
        return OrderAck(
            order_id=None,
            client_order_id=placement.client_order_id,
            status="FILLED",
            filled_qty=placement.quantity,
        )


def _as_decimal(value: object) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None
