"""Execution manager for LockBotBTC order plans."""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Callable, Dict, Iterable, Optional

import msgspec

from quantum_edge_core.strategies.legacy.lockbot.lockbot.contracts.lockbot_exec_v1 import EVENT_TYPES, ExecEnvelope
from quantum_edge_core.strategies.legacy.lockbot.lockbot_btc.ddn.config import DDNConfig
from quantum_edge_core.strategies.legacy.lockbot.lockbot_btc.ddn.engine import OrderPlan
from quantum_edge_core.strategies.legacy.lockbot.lockbot_btc.execution.base import (
    ExecutionConfig,
    ExecutionGate,
    ExecutionMode,
    SubmitResult,
)
from quantum_edge_core.strategies.legacy.lockbot.lockbot_btc.execution.binance_futures import BinanceFuturesExecutor
from quantum_edge_core.strategies.legacy.lockbot.lockbot_btc.execution.ledger import ExecutionLedger
from quantum_edge_core.strategies.legacy.lockbot.lockbot_btc.state.order_tracker import OrderTracker


class ExecutionManager:
    def __init__(
        self,
        *,
        config: ExecutionConfig,
        ddn_cfg: DDNConfig,
        bot_id: str,
        symbol: str,
        order_tracker: OrderTracker,
        ledger: ExecutionLedger,
        emit: Callable[[ExecEnvelope], None],
        logger: Optional[logging.Logger] = None,
        executor: Optional[BinanceFuturesExecutor] = None,
    ) -> None:
        self._cfg = config
        self._ddn_cfg = ddn_cfg
        self._bot_id = bot_id
        self._symbol = symbol
        self._tracker = order_tracker
        self._ledger = ledger
        self._emit = emit
        self._logger = logger or logging.getLogger(__name__)
        self._gate = ExecutionGate(mode=config.mode)
        self._executor = executor
        self._seq = 0
        self._submitted: Dict[str, str] = {}

    @property
    def gate(self) -> ExecutionGate:
        return self._gate

    def status_payload(self) -> Dict[str, object]:
        return {
            "armed": self._gate.armed,
            "mode": self._gate.mode.value,
            "auto_submit": self._cfg.auto_submit_on_allow,
            "arm_until_ms": self._gate.arm_until_ms,
            "last_error": self._gate.last_error,
            "disarm_reason": self._gate.disarm_reason,
            "error_count": self._gate.error_count,
            "open_orders": len(self._tracker.open_orders()),
        }

    def arm(
        self, mode: str, ttl_s: int, now_ms: int, cmd_id: Optional[str] = None
    ) -> None:
        exec_mode = ExecutionMode(mode)
        if exec_mode == ExecutionMode.LIVE_MAINNET and not self._cfg.allow_live_mainnet:
            self._gate.disarm("live_disabled")
            self._emit_event(
                "EXECUTION_DISABLED",
                now_ms,
                {"reason": "live_disabled", "cmd_id": cmd_id},
            )
            return
        self._gate.arm(exec_mode, ttl_s, now_ms)
        self._emit_event(
            "ORDER_ACKED",
            now_ms,
            {"reason": "armed", "mode": exec_mode.value, "cmd_id": cmd_id},
        )

    def disarm(self, reason: str, now_ms: int, cmd_id: Optional[str] = None) -> None:
        self._gate.disarm(reason)
        self._emit_event(
            "EXECUTION_DISABLED", now_ms, {"reason": reason, "cmd_id": cmd_id}
        )

    def cancel_all(
        self, reason: str, scope: str, now_ms: int, cmd_id: Optional[str] = None
    ) -> None:
        if not self._gate.is_armed(now_ms) or self._gate.mode == ExecutionMode.DRY_RUN:
            self._emit_event(
                "EXECUTION_DISABLED",
                now_ms,
                {"reason": "cancel_all_dry_run", "scope": scope, "cmd_id": cmd_id},
            )
            return
        executor = self._get_executor()
        if not executor:
            self._emit_event(
                "ORDER_REJECTED",
                now_ms,
                {"reason": "missing_executor", "scope": scope, "cmd_id": cmd_id},
            )
            return
        result = executor.cancel_all(symbol=self._symbol)
        payload = {
            "reason": reason,
            "scope": scope,
            "status": result.status,
            "cmd_id": cmd_id,
        }
        if result.ok:
            self._emit_event("ORDER_CANCELED", now_ms, payload)
        else:
            payload["error_code"] = result.error_code
            payload["error_detail"] = result.error_detail
            self._emit_event("ORDER_REJECTED", now_ms, payload)

    def on_tick(
        self, now_ms: int, account_lag_ms: Optional[int], bot_mode: str
    ) -> None:
        if (
            self._gate.armed
            and self._gate.arm_until_ms
            and now_ms > self._gate.arm_until_ms
        ):
            self.disarm("ttl_expired", now_ms)
        if account_lag_ms is not None and account_lag_ms > self._cfg.stale_account_ms:
            self.disarm("account_stale", now_ms)
        missing = self._tracker.missing_ack(now_ms, self._cfg.ack_timeout_ms)
        if missing:
            executor = None
            if self._gate.is_armed(now_ms) and self._gate.mode != ExecutionMode.DRY_RUN:
                executor = self._get_executor()
            for record in missing:
                payload = {
                    "reason": "missing_ack",
                    "client_order_id": record.client_order_id,
                }
                self._emit_event("RECONCILIATION_MISMATCH", now_ms, payload)
                if executor:
                    executor.cancel_order(
                        symbol=self._symbol, client_order_id=record.client_order_id
                    )
            self.disarm("missing_ack", now_ms)
        if bot_mode == "PANIC" and not self._cfg.allow_reduce_only_in_panic:
            self.disarm("panic_block", now_ms)

    def handle_order_update(self, update: dict, ts_ms: int, source: str) -> None:
        record = self._tracker.update_from_exchange(update, ts_ms, source)
        if record is None:
            payload = {
                "reason": "unknown_order",
                "client_order_id": update.get("clientOrderId"),
                "order_id": update.get("orderId"),
            }
            self._emit_event("RECONCILIATION_MISMATCH", ts_ms, payload)
            return
        status = str(update.get("status") or record.status)
        payload = {
            "cmd_id": record.cmd_id,
            "plan_id": record.plan_id,
            "client_order_id": record.client_order_id,
            "order_id": record.exchange_order_id,
            "side": record.side,
            "type": record.order_type,
            "qty": record.qty,
            "limit_price": record.limit_price,
            "reduce_only": record.reduce_only,
            "filled_qty": record.filled_qty,
            "avg_price": record.avg_price,
        }
        if status == "PARTIALLY_FILLED":
            self._emit_event("ORDER_PARTIALLY_FILLED", ts_ms, payload)
        elif status == "FILLED":
            self._emit_event("ORDER_FILLED", ts_ms, payload)
        elif status in {"CANCELED", "EXPIRED"}:
            self._emit_event("ORDER_CANCELED", ts_ms, payload)
        elif status == "REJECTED":
            self._emit_event("ORDER_REJECTED", ts_ms, payload)
        else:
            self._emit_event("ORDER_ACKED", ts_ms, payload)

    def submit_plans(
        self,
        *,
        cmd_id: str,
        plans: Iterable[OrderPlan],
        intent_action: str,
        now_ms: int,
        bot_mode: str,
        account_lag_ms: Optional[int],
        mark_price: Optional[float] = None,
    ) -> None:
        if not self._cfg.auto_submit_on_allow:
            for index, plan in enumerate(plans):
                payload = self._plan_payload(
                    cmd_id, plan, index, now_ms, "auto_submit_disabled"
                )
                self._emit_event("EXECUTION_DISABLED", now_ms, payload)
            return
        if not self._gate.is_armed(now_ms):
            for index, plan in enumerate(plans):
                payload = self._plan_payload(cmd_id, plan, index, now_ms, "disarmed")
                self._emit_event("EXECUTION_DISABLED", now_ms, payload)
            return
        if self._gate.mode == ExecutionMode.DRY_RUN:
            for index, plan in enumerate(plans):
                payload = self._plan_payload(cmd_id, plan, index, now_ms, "dry_run")
                self._emit_event("EXECUTION_DISABLED", now_ms, payload)
            return
        if (
            self._gate.mode == ExecutionMode.LIVE_MAINNET
            and not self._cfg.allow_live_mainnet
        ):
            self.disarm("live_disabled", now_ms)
            return
        if account_lag_ms is not None and account_lag_ms > self._cfg.stale_account_ms:
            self.disarm("account_stale", now_ms)
            return
        if bot_mode == "PAUSED":
            for index, plan in enumerate(plans):
                payload = self._plan_payload(cmd_id, plan, index, now_ms, "paused")
                self._emit_event("EXECUTION_DISABLED", now_ms, payload)
            return
        if bot_mode == "PANIC" and plan_requires_add(intent_action):
            for index, plan in enumerate(plans):
                payload = self._plan_payload(
                    cmd_id, plan, index, now_ms, "panic_add_blocked"
                )
                self._emit_event("EXECUTION_DISABLED", now_ms, payload)
            return
        if (
            self._tracker.open_orders()
            and len(self._tracker.open_orders()) >= self._cfg.max_open_orders
        ):
            for index, plan in enumerate(plans):
                payload = self._plan_payload(
                    cmd_id, plan, index, now_ms, "open_orders_cap"
                )
                self._emit_event("EXECUTION_DISABLED", now_ms, payload)
            return

        for index, plan in enumerate(plans):
            if self._symbol not in self._cfg.symbol_whitelist:
                payload = self._plan_payload(
                    cmd_id, plan, index, now_ms, "symbol_blocked"
                )
                self._emit_event("ORDER_REJECTED", now_ms, payload)
                continue
            if not self._valid_reduce_only(intent_action, plan.reduce_only):
                payload = self._plan_payload(
                    cmd_id, plan, index, now_ms, "reduce_only_mismatch"
                )
                self._emit_event("ORDER_REJECTED", now_ms, payload)
                continue
            if not self._within_limits(plan, mark_price):
                payload = self._plan_payload(cmd_id, plan, index, now_ms, "plan_limits")
                self._emit_event("ORDER_REJECTED", now_ms, payload)
                continue
            plan_id = make_plan_id(cmd_id, index, plan)
            client_order_id = make_client_order_id(cmd_id, index)
            if client_order_id in self._submitted:
                payload = self._plan_payload(
                    cmd_id, plan, index, now_ms, "ignored_duplicate"
                )
                self._emit_event("ORDER_SUBMITTED", now_ms, payload)
                continue
            record = self._tracker.record_submit(
                client_order_id=client_order_id,
                cmd_id=cmd_id,
                plan_id=plan_id,
                side=plan.side,
                order_type=plan.type,
                qty=plan.qty,
                reduce_only=plan.reduce_only,
                limit_price=plan.limit_price,
                ts_ms=now_ms,
            )
            submit_payload = self._plan_payload(cmd_id, plan, index, now_ms, None)
            submit_payload.update(
                {"plan_id": plan_id, "client_order_id": client_order_id}
            )
            self._emit_event("ORDER_SUBMITTED", now_ms, submit_payload)
            result = self._submit_plan(plan, client_order_id)
            self._submitted[client_order_id] = cmd_id
            if result.ok:
                record.exchange_order_id = result.order_id
                ack_payload = dict(submit_payload)
                ack_payload.update(
                    {"order_id": result.order_id, "status": result.status}
                )
                self._emit_event("ORDER_ACKED", now_ms, ack_payload)
            else:
                self._gate.note_error(result.error_detail or "submit_failed")
                reject_payload = dict(submit_payload)
                reject_payload.update(
                    {
                        "error_code": result.error_code,
                        "error_detail": result.error_detail,
                    }
                )
                self._emit_event("ORDER_REJECTED", now_ms, reject_payload)
                if self._gate.error_count >= self._cfg.error_threshold:
                    self.disarm("error_threshold", now_ms)

    def _submit_plan(self, plan: OrderPlan, client_order_id: str):
        executor = self._get_executor()
        if not executor:
            return SubmitResult(
                ok=False, client_order_id=client_order_id, error_code="missing_executor"
            )
        return executor.submit_order(
            symbol=self._symbol,
            side=plan.side,
            order_type=plan.type,
            qty=plan.qty,
            reduce_only=plan.reduce_only,
            client_order_id=client_order_id,
            price=plan.limit_price,
            time_in_force=plan.time_in_force,
        )

    def _get_executor(self) -> Optional[BinanceFuturesExecutor]:
        if self._executor:
            return self._executor
        if self._gate.mode == ExecutionMode.DRY_RUN:
            return None
        self._executor = BinanceFuturesExecutor(self._cfg)
        return self._executor

    def _emit_event(
        self, event_type: str, ts_ms: int, payload: Dict[str, object]
    ) -> None:
        if event_type not in EVENT_TYPES:
            return
        base_payload: Dict[str, object] = {
            "cmd_id": None,
            "plan_id": None,
            "client_order_id": None,
            "order_id": None,
            "side": None,
            "type": None,
            "qty": None,
            "limit_price": None,
            "reduce_only": None,
            "expected_cost_bps": None,
            "reason": None,
            "error_code": None,
            "error_detail": None,
            "filled_qty": None,
            "avg_price": None,
        }
        base_payload.update(payload)
        self._seq += 1
        envelope = ExecEnvelope(
            schema="lockbot_exec.v1",
            msg_type="exec",
            bot_id=self._bot_id,
            symbol=self._symbol,
            ts_event=ts_ms,
            seq=self._seq,
            event_type=event_type,
            payload=base_payload,
        )
        record = msgspec.structs.asdict(envelope)
        self._ledger.append(record)
        self._emit(envelope)

    def _plan_payload(
        self,
        cmd_id: str,
        plan: OrderPlan,
        index: int,
        ts_ms: int,
        reason: Optional[str],
    ) -> Dict[str, object]:
        payload: Dict[str, object] = {
            "cmd_id": cmd_id,
            "plan_id": make_plan_id(cmd_id, index, plan),
            "client_order_id": make_client_order_id(cmd_id, index),
            "side": plan.side,
            "type": plan.type,
            "qty": plan.qty,
            "limit_price": plan.limit_price,
            "reduce_only": plan.reduce_only,
            "expected_cost_bps": plan.expected_cost_bps,
            "ts_ms": ts_ms,
        }
        if reason:
            payload["reason"] = reason
        return payload

    def _within_limits(self, plan: OrderPlan, mark_price: Optional[float]) -> bool:
        if plan.qty <= 0:
            return False
        price = plan.limit_price or mark_price
        if price is None or price <= 0:
            return False
        if plan.qty * price > self._ddn_cfg.max_step_notional_usd:
            return False
        if plan.qty * price < self._ddn_cfg.min_step_notional_usd:
            return False
        return True

    @staticmethod
    def _valid_reduce_only(action: str, reduce_only: bool) -> bool:
        if action in {"TRIM_LONG", "TRIM_SHORT"} and not reduce_only:
            return False
        if action in {"ADD_LONG", "ADD_SHORT"} and reduce_only:
            return False
        return True


def make_client_order_id(cmd_id: str, index: int) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]", "", cmd_id)
    return f"LBTC_{cleaned[:12]}_{index:02d}"[:32]


def make_plan_id(cmd_id: str, index: int, plan: OrderPlan) -> str:
    payload = f"{cmd_id}:{index}:{plan.side}:{plan.type}:{plan.qty}:{plan.limit_price}:{plan.reduce_only}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]
    cleaned = re.sub(r"[^A-Za-z0-9]", "", cmd_id)
    return f"plan_{cleaned[:8]}_{index:02d}_{digest}"


def plan_requires_add(action: str) -> bool:
    return action in {"ADD_LONG", "ADD_SHORT"}
