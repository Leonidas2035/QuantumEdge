"""Order lifecycle tracker for LockBotBTC execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class OrderRecord:
    client_order_id: str
    cmd_id: str
    plan_id: str
    side: str
    order_type: str
    qty: float
    reduce_only: bool
    limit_price: Optional[float]
    status: str
    submitted_ts: int
    last_update_ts: int
    exchange_order_id: Optional[str] = None
    filled_qty: float = 0.0
    avg_price: Optional[float] = None
    last_source: str = "local"

    def is_open(self) -> bool:
        return self.status in {"SUBMITTED", "NEW", "PARTIALLY_FILLED"}


class OrderTracker:
    def __init__(self) -> None:
        self._by_client: Dict[str, OrderRecord] = {}
        self._by_order_id: Dict[str, OrderRecord] = {}

    def record_submit(
        self,
        *,
        client_order_id: str,
        cmd_id: str,
        plan_id: str,
        side: str,
        order_type: str,
        qty: float,
        reduce_only: bool,
        limit_price: Optional[float],
        ts_ms: int,
    ) -> OrderRecord:
        record = OrderRecord(
            client_order_id=client_order_id,
            cmd_id=cmd_id,
            plan_id=plan_id,
            side=side,
            order_type=order_type,
            qty=qty,
            reduce_only=reduce_only,
            limit_price=limit_price,
            status="SUBMITTED",
            submitted_ts=ts_ms,
            last_update_ts=ts_ms,
        )
        self._by_client[client_order_id] = record
        return record

    def update_from_exchange(self, update: dict, ts_ms: int, source: str) -> Optional[OrderRecord]:
        client_order_id = str(update.get("clientOrderId") or "")
        order_id = str(update.get("orderId") or "")
        record = None
        if client_order_id and client_order_id in self._by_client:
            record = self._by_client[client_order_id]
        elif order_id and order_id in self._by_order_id:
            record = self._by_order_id[order_id]
        if not record:
            return None
        status = str(update.get("status") or record.status)
        record.status = status
        record.last_update_ts = ts_ms
        record.last_source = source
        if order_id:
            record.exchange_order_id = order_id
            self._by_order_id[order_id] = record
        if update.get("executedQty") is not None:
            try:
                record.filled_qty = float(update.get("executedQty") or 0.0)
            except (TypeError, ValueError):
                pass
        if update.get("avgPrice") is not None:
            try:
                record.avg_price = float(update.get("avgPrice") or 0.0)
            except (TypeError, ValueError):
                pass
        return record

    def get(self, client_order_id: str) -> Optional[OrderRecord]:
        return self._by_client.get(client_order_id)

    def open_orders(self) -> list[OrderRecord]:
        return [record for record in self._by_client.values() if record.is_open()]

    def missing_ack(self, now_ms: int, timeout_ms: int) -> list[OrderRecord]:
        missing = []
        for record in self._by_client.values():
            if record.status == "SUBMITTED" and now_ms - record.submitted_ts > timeout_ms:
                missing.append(record)
        return missing
