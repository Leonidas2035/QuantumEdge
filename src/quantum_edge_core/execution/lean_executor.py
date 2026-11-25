"""Lean Execution Engine combining RiskGuard, SQLite OrderLedger, and BingX Hedge Mode intents."""

from __future__ import annotations

from typing import Any

from quantum_edge_core.contracts.lean import OrderResult, TradeDecision
from quantum_edge_core.execution.bingx_intent import build_bingx_order_params
from quantum_edge_core.execution.order_ledger import OrderLedger
from quantum_edge_core.execution.risk_guard import LeanRiskGuard


_ACTION_TO_INTENT = {
    "OPEN_LONG": "open_long",
    "OPEN_SHORT": "open_short",
    "CLOSE_LONG": "close_long",
    "CLOSE_SHORT": "close_short",
}


def _to_ccxt_swap_symbol(symbol: str) -> str:
    if "/" in symbol:
        return symbol
    if symbol.endswith("USDT") and len(symbol) > 4:
        return f"{symbol[:-4]}/USDT:USDT"
    return symbol


class LeanExecutor:
    """Practical, fail-safe BingX order executor without bureaucratic entry interlocks."""

    def __init__(
        self,
        exchange_client: Any,
        ledger: OrderLedger,
        risk_guard: LeanRiskGuard | None = None,
    ) -> None:
        self.exchange_client = exchange_client
        self.ledger = ledger
        self.risk_guard = risk_guard or LeanRiskGuard()

    def execute(
        self,
        decision: TradeDecision,
        current_price: float,
        open_positions_count: int = 0,
        position_qty: float | None = None,
    ) -> OrderResult:
        coid = self.ledger.make_client_order_id(
            decision.decision_id, decision.symbol, decision.action
        )

        # 1. Fast-path HOLD
        if decision.action == "HOLD":
            return OrderResult(
                client_order_id=coid,
                symbol=decision.symbol,
                action=decision.action,
                status="SKIPPED_HOLD",
            )

        # 2. 4-rule Risk Guard validation
        allowed, reason = self.risk_guard.validate(
            decision=decision,
            open_positions_count=open_positions_count,
            current_price=current_price,
        )
        if not allowed:
            return OrderResult(
                client_order_id=coid,
                symbol=decision.symbol,
                action=decision.action,
                status="REJECTED",
                error=reason,
            )

        # 3. Idempotent SQLite reservation
        if not self.ledger.reserve(
            client_order_id=coid,
            decision_id=decision.decision_id,
            symbol=decision.symbol,
            action=decision.action,
        ):
            return OrderResult(
                client_order_id=coid,
                symbol=decision.symbol,
                action=decision.action,
                status="REJECTED",
                error="DUPLICATE_CLIENT_ORDER_ID",
            )

        # 4. Canonical BingX Hedge Mode parameters
        intent = _ACTION_TO_INTENT[decision.action]
        mapped = build_bingx_order_params(intent, hedged=True)
        params: dict[str, Any] = {
            "positionSide": mapped["positionSide"],
            "reduceOnly": mapped["reduceOnly"],
            "hedged": mapped["hedged"],
            "clientOrderId": coid,
        }

        if not mapped["reduceOnly"]:
            if decision.sl_price is not None and decision.sl_price > 0:
                params["stopLoss"] = {"triggerPrice": float(decision.sl_price)}
            if decision.tp_price is not None and decision.tp_price > 0:
                params["takeProfit"] = {"triggerPrice": float(decision.tp_price)}

        order_type = decision.order_type.lower()
        ref_price = (
            float(decision.entry_price)
            if (decision.entry_price is not None and decision.entry_price > 0)
            else float(current_price)
        )
        limit_price = ref_price if order_type == "limit" else None

        if decision.qty is not None and decision.qty > 0:
            amount = float(decision.qty)
        elif position_qty is not None and position_qty > 0:
            amount = float(position_qty)
        elif decision.margin_vst > 0 and ref_price > 0:
            amount = round((decision.margin_vst * decision.leverage) / ref_price, 6)
        else:
            amount = 0.0

        ccxt_symbol = _to_ccxt_swap_symbol(decision.symbol)

        # 5. Submit to exchange without blind retries on timeout/exception
        try:
            order = self.exchange_client.create_order(
                symbol=ccxt_symbol,
                type=order_type,
                side=mapped["side"],
                amount=amount,
                price=limit_price,
                params=params,
            )
        except Exception as exc:
            err_msg = str(exc)
            self.ledger.update_status(coid, status="UNKNOWN", error=err_msg)
            return OrderResult(
                client_order_id=coid,
                symbol=decision.symbol,
                action=decision.action,
                status="UNKNOWN",
                error=err_msg,
            )

        ex_id = (
            str(order["id"])
            if isinstance(order, dict) and order.get("id") is not None
            else None
        )
        raw_status = (
            str(order.get("status") or "").lower()
            if isinstance(order, dict)
            else ""
        )
        final_status = "FILLED" if raw_status in ("closed", "filled") else "SUBMITTED"
        filled_qty = (
            float(order["filled"])
            if isinstance(order, dict) and order.get("filled") is not None
            else (amount if final_status == "FILLED" else 0.0)
        )
        avg_price = (
            float(order["average"])
            if isinstance(order, dict) and order.get("average") is not None
            else (
                float(order["price"])
                if isinstance(order, dict) and order.get("price") is not None
                else limit_price
            )
        )

        self.ledger.update_status(
            coid,
            status=final_status,
            exchange_order_id=ex_id,
        )
        return OrderResult(
            client_order_id=coid,
            symbol=decision.symbol,
            action=decision.action,
            status=final_status,
            exchange_order_id=ex_id,
            filled_qty=filled_qty,
            avg_price=avg_price,
        )
