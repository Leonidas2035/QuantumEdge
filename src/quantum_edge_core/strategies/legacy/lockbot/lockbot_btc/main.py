"""LockBotBTC service entry point."""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

import msgspec

from LockBotBTC.lockbot.contracts.lockbot_control_v1 import (
    ACK_TOPIC,
    CMD_TOPIC,
    STATUS_TOPIC,
    AckEnvelope,
    StatusEnvelope,
    validate_command,
)
from LockBotBTC.lockbot.contracts.lockbot_exec_v1 import EXEC_TOPIC, ExecEnvelope
from LockBotBTC.lockbot_btc.config import LockbotConfig
from LockBotBTC.lockbot_btc.ddn.engine import (
    DDNContext,
    DDNEngine,
    DDNIntent,
    DDNMarketSnapshot,
    DDNPositionSnapshot,
)
from LockBotBTC.lockbot_btc.execution.ledger import ExecutionLedger
from LockBotBTC.lockbot_btc.execution.manager import ExecutionManager
from LockBotBTC.lockbot_btc.ipc.control_subscriber import ControlSubscriber
from LockBotBTC.lockbot_btc.ipc.hub_subscriber import HubSubscriber
from LockBotBTC.lockbot_btc.ipc.publisher import BotPublisher
from LockBotBTC.lockbot_btc.ipc.raw_subscriber import RawSubscriber
from LockBotBTC.lockbot_btc.state.account_state import AccountState
from LockBotBTC.lockbot_btc.state.bot_state import BotState
from LockBotBTC.lockbot_btc.state.market_state import MarketState
from LockBotBTC.lockbot_btc.state.order_tracker import OrderTracker


class LockBotService:
    def __init__(self, cfg: LockbotConfig, *, ipc_enabled: bool = True) -> None:
        self._cfg = cfg
        self._bot_state = BotState(bot_id=cfg.bot_id, symbol=cfg.symbol)
        self._bot_state.configure_cache(cfg.cmd_cache_size)
        self._market_state = MarketState(volatility_window=cfg.ddn.volatility_window)
        self._account_state = AccountState()
        self._ddn = DDNEngine(cfg.ddn)
        self._order_tracker = OrderTracker()
        self._exec_ledger = ExecutionLedger(cfg.execution.ledger_path, logging.getLogger(__name__))
        self._ipc_enabled = ipc_enabled
        self._publisher = BotPublisher(cfg.bot_pub_endpoint) if ipc_enabled else _NullPublisher()
        self._exec_manager = ExecutionManager(
            config=cfg.execution,
            ddn_cfg=cfg.ddn,
            bot_id=cfg.bot_id,
            symbol=cfg.symbol,
            order_tracker=self._order_tracker,
            ledger=self._exec_ledger,
            emit=self._emit_exec_event,
            logger=logging.getLogger(__name__),
        )
        self._hub_sub = HubSubscriber(cfg.hub_sub_endpoint, cfg.market_topics) if ipc_enabled else None
        self._cmd_sub = ControlSubscriber(cfg.supervisor_cmd_sub_endpoint, CMD_TOPIC) if ipc_enabled else None
        self._account_sub: Optional[RawSubscriber] = None
        if ipc_enabled and cfg.account_topics:
            self._account_sub = RawSubscriber(cfg.hub_sub_endpoint, cfg.account_topics)
        self._tasks: list[asyncio.Task] = []
        self._stop = asyncio.Event()
        self._seq = 0
        self._start_ts = time.time()
        self._loop_counter = 0
        self._last_loop_ts = time.time()
        self._dropped_msgs = 0

    async def start(self) -> None:
        if self._hub_sub:
            await self._hub_sub.start()
        if self._cmd_sub:
            await self._cmd_sub.start()
        if self._account_sub:
            await self._account_sub.start()
        self._stop.clear()
        self._tasks = []
        if self._hub_sub:
            self._tasks.append(asyncio.create_task(self._market_loop()))
        if self._cmd_sub:
            self._tasks.append(asyncio.create_task(self._cmd_loop()))
        if self._ipc_enabled:
            self._tasks.append(asyncio.create_task(self._heartbeat_loop()))
        if self._account_sub:
            self._tasks.append(asyncio.create_task(self._account_loop()))

    async def stop(self) -> None:
        self._stop.set()
        for task in self._tasks:
            task.cancel()
        if self._hub_sub:
            await self._hub_sub.stop()
        if self._cmd_sub:
            await self._cmd_sub.stop()
        if self._account_sub:
            await self._account_sub.stop()
        self._publisher.close()

    def process_command(self, command: Dict[str, Any], *, now_ms: Optional[int] = None) -> AckEnvelope:
        ok, reason = validate_command(command)
        cmd_id = str(command.get("cmd_id") or "")
        now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
        ttl_ms = int(command.get("ttl_ms") or self._cfg.cmd_ttl_ms)
        if self._bot_state.is_duplicate(cmd_id):
            return self._build_ack(cmd_id, "IGNORED_DUPLICATE", state_version=self._bot_state.state_version, now_ms=now_ms)
        if not ok:
            return self._build_ack(cmd_id, "REJECTED", error_code=reason, state_version=self._bot_state.state_version, now_ms=now_ms)
        ts_cmd = int(command.get("ts_cmd") or 0)
        if ts_cmd + ttl_ms < now_ms:
            self._bot_state.remember_cmd(cmd_id)
            return self._build_ack(cmd_id, "EXPIRED", error_code="ttl", state_version=self._bot_state.state_version, now_ms=now_ms)

        payload = command.get("payload") if isinstance(command.get("payload"), dict) else {}
        cmd_type = payload.get("cmd")
        if cmd_type:
            self._bot_state.record_command(cmd_id, str(cmd_type), now_ms, payload)
        if cmd_type in {"ARM_EXECUTION", "DISARM_EXECUTION", "CANCEL_ALL"}:
            ack_status, error_code = self._handle_exec_command(cmd_type, payload, now_ms, cmd_id)
            self._bot_state.remember_cmd(cmd_id)
            self._bot_state.bump_state()
            return self._build_ack(cmd_id, ack_status, error_code=error_code, state_version=self._bot_state.state_version, now_ms=now_ms)
        intent = self._build_intent(cmd_type, payload)
        decision = self._ddn.evaluate(self._build_ddn_context(intent, now_ms=now_ms))
        verdict = decision.verdict
        self._bot_state.record_decision(
            verdict=verdict,
            reasons=decision.reasons,
            step_qty=decision.recommended_step_qty,
            cost_bps=decision.expected_cost_bps,
            plans=[plan.__dict__ for plan in decision.order_plans],
        )
        if decision.adjusted_target is not None:
            self._bot_state.ddn_target = decision.adjusted_target
        if decision.adjusted_band_low is not None:
            self._bot_state.ddn_band_low = decision.adjusted_band_low
        if decision.adjusted_band_high is not None:
            self._bot_state.ddn_band_high = decision.adjusted_band_high
        if intent.profile:
            self._bot_state.ddn_profile = intent.profile
        if cmd_type == "SET_REGIME":
            self._bot_state.regime = str(payload.get("regime"))
        elif cmd_type == "PAUSE":
            self._bot_state.mode = "PAUSED"
        elif cmd_type == "RESUME":
            self._bot_state.mode = "IDLE"
        elif cmd_type == "PANIC_LOCK":
            self._bot_state.mode = "PANIC"
        elif cmd_type == "EXIT_LOCK":
            self._bot_state.mode = "EXITING"
        elif cmd_type in {"EXEC_STEP", "SET_DELTA_TARGET"}:
            if self._bot_state.mode == "IDLE":
                self._bot_state.mode = "LOCKED"
        self._bot_state.remember_cmd(cmd_id)
        self._bot_state.bump_state()
        ack_status = "ACCEPTED" if verdict in {"ALLOW", "MODIFY", "PANIC_ONLY"} else "REJECTED"
        error_code = decision.reasons[0] if ack_status == "REJECTED" and decision.reasons else None
        if verdict in {"ALLOW", "MODIFY", "PANIC_ONLY"} and decision.order_plans:
            account_lag = _lag_ms(now_ms, self._account_state.last_account_ts)
            self._exec_manager.submit_plans(
                cmd_id=cmd_id,
                plans=decision.order_plans,
                intent_action=intent.action,
                now_ms=now_ms,
                bot_mode=self._bot_state.mode,
                account_lag_ms=account_lag,
                mark_price=self._market_state.mark_price,
            )
        return self._build_ack(cmd_id, ack_status, error_code=error_code, state_version=self._bot_state.state_version, now_ms=now_ms)

    def build_status(self, *, now_ms: Optional[int] = None) -> StatusEnvelope:
        now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
        self._update_execution_state()
        market_lag = _lag_ms(now_ms, self._market_state.last_market_ts)
        account_lag = _lag_ms(now_ms, self._account_state.last_account_ts)
        loop_hz = self._calc_loop_hz()
        payload = {
            "mode": self._bot_state.mode,
            "regime": self._bot_state.regime,
            "net_delta_est": self._account_state.net_delta_est(),
            "risk": {
                "margin_usage": self._account_state.margin_usage,
                "distance_to_liq_bps": self._account_state.distance_to_liq_bps,
                "funding_rate": self._market_state.funding_rate,
            },
            "positions": {
                "long_qty": self._account_state.long_qty,
                "short_qty": self._account_state.short_qty,
                "long_avg_px": self._account_state.long_avg_px,
                "short_avg_px": self._account_state.short_avg_px,
            },
            "market": {
                "mark_price": self._market_state.mark_price,
                "vwap_d": self._market_state.vwap_d,
                "band_1u": self._market_state.band_1u,
                "band_1l": self._market_state.band_1l,
                "band_2u": self._market_state.band_2u,
                "band_2l": self._market_state.band_2l,
            },
            "lags": {
                "last_market_ts": self._market_state.last_market_ts,
                "last_account_ts": self._account_state.last_account_ts,
                "market_lag_ms": market_lag,
                "account_lag_ms": account_lag,
            },
            "health": {
                "uptime_s": int(time.time() - self._start_ts),
                "loop_hz": loop_hz,
                "dropped_msgs": self._dropped_msgs,
                "last_error": self._bot_state.last_error,
            },
            "ddn": {
                "last_verdict": self._bot_state.last_ddn_verdict,
                "last_reasons": self._bot_state.last_ddn_reasons,
                "last_step_qty": self._bot_state.last_ddn_step_qty,
                "last_cost_bps": self._bot_state.last_ddn_cost_bps,
                "order_plans": list(self._bot_state.last_order_plans),
            },
            "policy": {
                "last_cmd_type": self._bot_state.last_cmd_type,
                "last_cmd_id": self._bot_state.last_cmd_id,
                "last_cmd_ts": self._bot_state.last_cmd_ts,
                "last_cmd_payload": self._bot_state.last_cmd_payload,
                "ddn_profile": self._bot_state.ddn_profile,
                "ddn_target": self._bot_state.ddn_target,
                "ddn_band_low": self._bot_state.ddn_band_low,
                "ddn_band_high": self._bot_state.ddn_band_high,
            },
            "execution": {
                "armed": self._bot_state.execution_armed,
                "mode": self._bot_state.execution_mode,
                "disarm_reason": self._bot_state.execution_disarm_reason,
                "last_error": self._bot_state.execution_last_error,
                "error_count": self._bot_state.execution_error_count,
                "open_orders": self._bot_state.execution_open_orders,
                "last_event": self._bot_state.execution_last_event,
                "last_event_ts": self._bot_state.execution_last_event_ts,
                "auto_submit": self._cfg.execution.auto_submit_on_allow,
            },
        }
        self._seq += 1
        return StatusEnvelope(
            schema="lockbot_control.v1",
            msg_type="status",
            bot_id=self._cfg.bot_id,
            symbol=self._cfg.symbol,
            ts_event=now_ms,
            seq=self._seq,
            payload=payload,
        )

    def handle_market_event(self, event: Any) -> None:
        event_type = getattr(event, "event_type", None) or event.get("event_type")
        payload = getattr(event, "payload", None) or event.get("payload", {})
        ts_event = getattr(event, "ts_event", None) or event.get("ts_event") or 0
        if event_type == "mark_price_1s":
            mark_price = payload.get("mark_price")
            if mark_price is not None:
                self._market_state.update_mark_price(float(mark_price))
            if "funding_rate" in payload:
                self._market_state.funding_rate = payload.get("funding_rate")
        elif event_type == "vwap_d":
            self._market_state.vwap_d = payload.get("vwap")
        elif event_type == "vwap_bands_d":
            self._market_state.band_1u = payload.get("band_1u")
            self._market_state.band_1l = payload.get("band_1l")
            self._market_state.band_2u = payload.get("band_2u")
            self._market_state.band_2l = payload.get("band_2l")
        elif event_type == "avwap":
            self._market_state.avwap = payload
        elif event_type == "liq_heatmap":
            self._market_state.liq_heatmap = payload
        if ts_event:
            self._market_state.update_timestamp(int(ts_event))

    async def _market_loop(self) -> None:
        if not self._hub_sub:
            return
        async for _topic, event in self._hub_sub.events():
            self._loop_counter += 1
            self.handle_market_event(event)

    def handle_account_payload(self, payload: Dict[str, Any]) -> None:
        ts_event = payload.get("ts_event") or payload.get("ts_ms")
        if ts_event is None and payload.get("ts_ms"):
            ts_event = payload.get("ts_ms")
        if ts_event is not None:
            self._account_state.update_timestamp(int(ts_event))
        if payload.get("schema") in {"hub.account_delta.v1", "hub.account_snapshot.v1"}:
            self._apply_hub_account_payload(payload, int(ts_event or 0))
            return
        positions = payload.get("positions")
        if isinstance(positions, dict):
            self._account_state.long_qty = positions.get("long_qty", self._account_state.long_qty)
            self._account_state.short_qty = positions.get("short_qty", self._account_state.short_qty)
            self._account_state.long_avg_px = positions.get("long_avg_px", self._account_state.long_avg_px)
            self._account_state.short_avg_px = positions.get("short_avg_px", self._account_state.short_avg_px)
            self._account_state.liq_price_long = positions.get("liq_price_long", self._account_state.liq_price_long)
            self._account_state.liq_price_short = positions.get("liq_price_short", self._account_state.liq_price_short)
        risk = payload.get("risk")
        if isinstance(risk, dict):
            self._account_state.margin_usage = risk.get("margin_usage", self._account_state.margin_usage)
            self._account_state.distance_to_liq_bps = risk.get("distance_to_liq_bps", self._account_state.distance_to_liq_bps)
            self._account_state.initial_margin = risk.get("initial_margin", self._account_state.initial_margin)
            self._account_state.maintenance_margin = risk.get("maintenance_margin", self._account_state.maintenance_margin)
            self._account_state.equity = risk.get("equity", self._account_state.equity)
            self._account_state.leverage = risk.get("leverage", self._account_state.leverage)

    async def _account_loop(self) -> None:
        if not self._account_sub:
            return
        async for _topic, payload in self._account_sub.events():
            self._loop_counter += 1
            self.handle_account_payload(payload)

    async def _cmd_loop(self) -> None:
        if not self._cmd_sub:
            return
        async for cmd in self._cmd_sub.commands():
            self._loop_counter += 1
            command = msgspec.structs.asdict(cmd)
            ack = self.process_command(command)
            self._publisher.publish_ack(ACK_TOPIC, ack)
            status = self.build_status()
            self._publisher.publish_status(STATUS_TOPIC, status)

    async def _heartbeat_loop(self) -> None:
        interval = max(self._cfg.heartbeat_interval_ms / 1000.0, 0.2)
        while not self._stop.is_set():
            await asyncio.sleep(interval)
            now_ms = int(time.time() * 1000)
            account_lag = _lag_ms(now_ms, self._account_state.last_account_ts)
            self._exec_manager.on_tick(now_ms, account_lag, self._bot_state.mode)
            status = self.build_status()
            self._publisher.publish_status(STATUS_TOPIC, status)

    def _build_ack(
        self,
        cmd_id: str,
        status: str,
        *,
        error_code: Optional[str] = None,
        error_detail: Optional[str] = None,
        state_version: int = 0,
        now_ms: Optional[int] = None,
    ) -> AckEnvelope:
        payload = {"status": status, "state_version": state_version}
        if error_code:
            payload["error_code"] = error_code
        if error_detail:
            payload["error_detail"] = error_detail
        return AckEnvelope(
            schema="lockbot_control.v1",
            msg_type="ack",
            bot_id=self._cfg.bot_id,
            symbol=self._cfg.symbol,
            cmd_id=cmd_id,
            ts_ack=now_ms if now_ms is not None else int(time.time() * 1000),
            payload=payload,
        )

    def _calc_loop_hz(self) -> float:
        now = time.time()
        elapsed = max(now - self._last_loop_ts, 1e-6)
        loop_hz = self._loop_counter / elapsed
        self._loop_counter = 0
        self._last_loop_ts = now
        return round(loop_hz, 3)

    def _build_intent(self, cmd_type: str, payload: Dict[str, Any]) -> DDNIntent:
        if cmd_type == "SET_REGIME":
            regime = str(payload.get("regime", "")).upper()
            profile = "neutral"
            if regime in {"TREND_UP", "TREND_DOWN"}:
                profile = "trend"
            elif regime == "CHAOS":
                profile = "neutral"
            return DDNIntent(action="SET_REGIME", profile=profile, reason=payload.get("reason"))
        if cmd_type == "SET_DELTA_TARGET":
            return DDNIntent(
                action="SET_DELTA_TARGET",
                target=payload.get("target"),
                band_low=payload.get("band_low"),
                band_high=payload.get("band_high"),
                reason=payload.get("reason"),
                expected_edge_bps=payload.get("expected_edge_bps"),
            )
        if cmd_type == "EXEC_STEP":
            return DDNIntent(
                action=str(payload.get("action")),
                qty_hint=payload.get("qty_hint"),
                reason=payload.get("reason"),
                expected_edge_bps=payload.get("expected_edge_bps"),
            )
        if cmd_type == "PANIC_LOCK":
            return DDNIntent(action="PANIC_LOCK", reason=payload.get("reason"))
        if cmd_type == "EXIT_LOCK":
            return DDNIntent(action="EXIT_LOCK", reason=payload.get("reason"))
        if cmd_type == "PAUSE":
            return DDNIntent(action="PAUSE", reason=payload.get("reason"))
        if cmd_type == "RESUME":
            return DDNIntent(action="RESUME", reason=payload.get("reason"))
        if cmd_type in {"ARM_EXECUTION", "DISARM_EXECUTION", "CANCEL_ALL"}:
            return DDNIntent(action=cmd_type, reason=payload.get("reason"))
        return DDNIntent(action=str(cmd_type))

    def _handle_exec_command(
        self, cmd_type: str, payload: Dict[str, Any], now_ms: int, cmd_id: str
    ) -> tuple[str, Optional[str]]:
        if cmd_type == "ARM_EXECUTION":
            mode = str(payload.get("mode") or "")
            ttl_s = payload.get("ttl_s")
            if not mode or ttl_s is None:
                return "REJECTED", "invalid_arm"
            try:
                self._exec_manager.arm(mode, int(ttl_s), now_ms, cmd_id=cmd_id)
            except ValueError:
                return "REJECTED", "mode"
            if not self._exec_manager.gate.armed:
                return "REJECTED", self._exec_manager.gate.disarm_reason or "disarmed"
            return "ACCEPTED", None
        if cmd_type == "DISARM_EXECUTION":
            self._exec_manager.disarm(str(payload.get("reason") or "manual_disarm"), now_ms, cmd_id=cmd_id)
            return "ACCEPTED", None
        if cmd_type == "CANCEL_ALL":
            scope = str(payload.get("scope") or "OPEN_ONLY")
            self._exec_manager.cancel_all(str(payload.get("reason") or "manual_cancel"), scope, now_ms, cmd_id=cmd_id)
            return "ACCEPTED", None
        return "REJECTED", "unsupported"

    def _update_execution_state(self) -> None:
        payload = self._exec_manager.status_payload()
        self._bot_state.execution_armed = bool(payload.get("armed"))
        self._bot_state.execution_mode = str(payload.get("mode") or "DRY_RUN")
        self._bot_state.execution_disarm_reason = payload.get("disarm_reason")
        self._bot_state.execution_last_error = payload.get("last_error")
        self._bot_state.execution_error_count = int(payload.get("error_count") or 0)
        self._bot_state.execution_open_orders = int(payload.get("open_orders") or 0)

    def _emit_exec_event(self, event: ExecEnvelope) -> None:
        self._bot_state.execution_last_event = event.event_type
        self._bot_state.execution_last_event_ts = event.ts_event
        if self._ipc_enabled:
            self._publisher.publish_exec(EXEC_TOPIC, event)

    def _apply_hub_account_payload(self, payload: Dict[str, Any], ts_event: int) -> None:
        patch = payload.get("patch", {})
        if payload.get("schema") == "hub.account_delta.v1":
            usdm = patch.get("usdm", {}) if isinstance(patch, dict) else {}
            orders = usdm.get("orders_update") if isinstance(usdm, dict) else None
            positions = usdm.get("positions_update") if isinstance(usdm, dict) else None
            account_totals = usdm.get("account_update") if isinstance(usdm, dict) else None
            if isinstance(account_totals, dict):
                self._account_state.equity = _to_float(account_totals.get("totalMarginBalance"))
                self._account_state.initial_margin = _to_float(account_totals.get("totalWalletBalance"))
                self._account_state.maintenance_margin = _to_float(account_totals.get("totalUnrealizedProfit"))
            if isinstance(positions, list):
                self._apply_positions_update(positions)
            if isinstance(orders, list):
                for order in orders:
                    if isinstance(order, dict):
                        self._exec_manager.handle_order_update(order, ts_event, "account_delta")
        if payload.get("schema") == "hub.account_snapshot.v1":
            usdm = payload.get("usdm", {}) if isinstance(payload.get("usdm"), dict) else {}
            positions = usdm.get("positions") if isinstance(usdm, dict) else None
            orders = usdm.get("open_orders") if isinstance(usdm, dict) else None
            if isinstance(positions, list):
                self._apply_positions_update(positions)
            if isinstance(orders, list):
                for order in orders:
                    if isinstance(order, dict):
                        self._exec_manager.handle_order_update(order, ts_event, "account_snapshot")

    def _apply_positions_update(self, positions: list[dict]) -> None:
        long_qty = 0.0
        short_qty = 0.0
        long_avg = None
        short_avg = None
        liq_long = None
        liq_short = None
        for pos in positions:
            amt = _to_float(pos.get("positionAmt"))
            if amt is None:
                continue
            if amt >= 0:
                long_qty = abs(amt)
                long_avg = _to_float(pos.get("entryPrice"))
                liq_long = _to_float(pos.get("liquidationPrice"))
            else:
                short_qty = abs(amt)
                short_avg = _to_float(pos.get("entryPrice"))
                liq_short = _to_float(pos.get("liquidationPrice"))
        if long_qty:
            self._account_state.long_qty = long_qty
            self._account_state.long_avg_px = long_avg
        if short_qty:
            self._account_state.short_qty = short_qty
            self._account_state.short_avg_px = short_avg
        if liq_long:
            self._account_state.liq_price_long = liq_long
        if liq_short:
            self._account_state.liq_price_short = liq_short

    def _build_ddn_context(self, intent: DDNIntent, *, now_ms: Optional[int] = None) -> DDNContext:
        profile = self._cfg.ddn.profiles.get(self._bot_state.ddn_profile) or self._cfg.ddn.profiles.get("neutral")
        if profile is None:
            profile = self._cfg.ddn.__class__.default().profiles["neutral"]
        active_profile = profile
        use_defaults = False
        if intent.profile and intent.profile in self._cfg.ddn.profiles:
            active_profile = self._cfg.ddn.profiles[intent.profile]
            if intent.action == "SET_REGIME":
                use_defaults = True
        target = active_profile.target if use_defaults else self._bot_state.ddn_target
        band_low = active_profile.band_low if use_defaults else self._bot_state.ddn_band_low
        band_high = active_profile.band_high if use_defaults else self._bot_state.ddn_band_high
        active_profile = active_profile.__class__(
            name=active_profile.name,
            target=target,
            band_low=band_low,
            band_high=band_high,
            force_hedge=active_profile.force_hedge,
        )
        now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
        market_lag = _lag_ms(now_ms, self._market_state.last_market_ts)
        account_lag = _lag_ms(now_ms, self._account_state.last_account_ts)
        if self._account_sub is None:
            account_lag = 0
        margin_usage = self._account_state.margin_usage
        if margin_usage is None:
            margin_usage = self._account_state.compute_margin_usage()
        distance_bps = self._account_state.distance_to_liq_bps
        if distance_bps is None:
            distance_bps = self._account_state.compute_distance_to_liq_bps(self._market_state.mark_price)
        market = DDNMarketSnapshot(
            mark_price=self._market_state.mark_price,
            vwap_d=self._market_state.vwap_d,
            bands={
                "band_1u": self._market_state.band_1u,
                "band_1l": self._market_state.band_1l,
                "band_2u": self._market_state.band_2u,
                "band_2l": self._market_state.band_2l,
            },
            funding_rate=self._market_state.funding_rate,
            volatility_bps=self._market_state.volatility_bps,
            market_lag_ms=market_lag,
        )
        position = DDNPositionSnapshot(
            long_qty=self._account_state.long_qty,
            short_qty=self._account_state.short_qty,
            margin_usage=margin_usage,
            distance_to_liq_bps=distance_bps,
            account_lag_ms=account_lag,
        )
        return DDNContext(
            intent=intent,
            market=market,
            position=position,
            profile=active_profile,
            max_band_abs=self._cfg.ddn.max_band_abs,
        )


def _lag_ms(now_ms: int, last_ts_ms: Optional[int]) -> Optional[int]:
    if last_ts_ms is None:
        return None
    return max(0, now_ms - int(last_ts_ms))


def _to_float(value: object) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class _NullPublisher:
    def publish_ack(self, _topic: str, _ack: AckEnvelope) -> None:
        return None

    def publish_status(self, _topic: str, _status: StatusEnvelope) -> None:
        return None

    def close(self) -> None:
        return None


async def _run(config_path: Optional[Path]) -> None:
    cfg = LockbotConfig.load(config_path)
    log_path = Path(cfg.log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=cfg.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8"), logging.StreamHandler()],
    )
    service = LockBotService(cfg)
    await service.start()
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        await service.stop()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config/lockbot_btc.yaml"))
    args = parser.parse_args()
    asyncio.run(_run(args.config))


if __name__ == "__main__":
    main()
