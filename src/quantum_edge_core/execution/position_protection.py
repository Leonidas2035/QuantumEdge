"""Autonomous 5-second deterministic PositionProtectionWatcher (zero LLM dependencies).

Enforces orphan protective-order cleanup in FLAT state and emergency position
exits on liquidation proximity or missing-SL + stale-feed strictly through the
single canonical HarnessBridge -> LeanExecutor -> OrderLedger pipeline.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Mapping, Sequence

from quantum_edge_core.contracts.lean import TradeDecision
from quantum_edge_core.execution.bingx_vst_adapter import (
    cancel_vst_open_orders,
    fetch_vst_status,
    is_vst_sandbox_client,
    move_vst_stop_loss,
    to_ccxt_swap_symbol,
)
from quantum_edge_core.execution.harness_bridge import HarnessBridge

logger = logging.getLogger("quantum_edge_core.execution.position_protection")

DEFAULT_ALLOWED_SYMBOLS: tuple[str, ...] = (
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "BNBUSDT",
    "LINKUSDT",
    "SUIUSDT",
)


class PositionProtectionWatcher:
    """Always-on 5-second deterministic position safety loop (0 LLM tokens, single writer)."""

    def __init__(
        self,
        exchange_client: Any,
        bridge: HarnessBridge,
        *,
        allowed_symbols: Sequence[str] = DEFAULT_ALLOWED_SYMBOLS,
        order_id_prefix: str | None = "qe-",
        strict_prefix: bool = False,
        interval_sec: float = 5.0,
        max_liq_distance_pct: float = 2.0,
        stale_feed_limit_sec: float = 30.0,
        enable_breakeven_sl: bool = True,
        breakeven_trigger_pct: float = 0.40,
        breakeven_offset_pct: float = 0.12,
        dry_run: bool = False,
    ) -> None:
        self.client = exchange_client
        self.bridge = bridge
        self.allowed_symbols: frozenset[str] = frozenset(
            str(s).strip().upper() for s in allowed_symbols if str(s).strip()
        )
        self.order_id_prefix = order_id_prefix.strip() if order_id_prefix else None
        self.strict_prefix = bool(strict_prefix)
        self.interval_sec = max(interval_sec, 1.0)
        self.max_liq_distance_pct = max_liq_distance_pct
        self.stale_feed_limit_sec = stale_feed_limit_sec
        self.enable_breakeven_sl = bool(enable_breakeven_sl)
        self.breakeven_trigger_pct = float(breakeven_trigger_pct)
        self.breakeven_offset_pct = float(breakeven_offset_pct)
        self.dry_run = dry_run
        self._last_feed_ts = time.time()
        self._stop = asyncio.Event()

    def _is_managed_order(self, order: Mapping[str, Any]) -> bool:
        """Check whether an open order belongs to QuantumEdge via clientOrderId prefix."""
        if not self.order_id_prefix:
            return True
        info = order.get("info") if isinstance(order.get("info"), Mapping) else {}
        coid = str(
            order.get("clientOrderId")
            or info.get("clientOrderId")
            or info.get("clientOrderID")
            or ""
        ).strip()
        if coid:
            return coid.startswith(self.order_id_prefix)
        return not self.strict_prefix

    def touch_feed_heartbeat(self, ts_sec: float | None = None) -> None:
        self._last_feed_ts = ts_sec if ts_sec is not None else time.time()

    async def run_forever(self) -> None:
        while not self._stop.is_set():
            try:
                await self.check_once()
            except Exception as exc:
                logger.error("PositionProtectionWatcher check failed: %s", exc, exc_info=True)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_sec)
            except asyncio.TimeoutError:
                continue

    def stop(self) -> None:
        self._stop.set()

    async def check_once(self) -> dict[str, Any]:
        status = await asyncio.to_thread(fetch_vst_status, self.client)
        all_positions = status.get("open_positions") or []
        all_orders = status.get("open_orders") or []
        actions_taken: list[str] = []

        positions = [
            p for p in all_positions if str(p.get("symbol") or "").upper() in self.allowed_symbols
        ]
        scoped_orders = [
            o for o in all_orders if str(o.get("symbol") or "").upper() in self.allowed_symbols
        ]
        managed_orders = [o for o in scoped_orders if self._is_managed_order(o)]
        ignored_foreign_positions = len(all_positions) - len(positions)
        ignored_foreign_orders = len(all_orders) - len(managed_orders)

        # 1. Clean up orphan protective orders per symbol when that symbol has NO active position
        active_pos_symbols = {
            str(p.get("symbol") or "").upper()
            for p in positions
            if str(p.get("symbol") or "").strip()
        }
        reduce_only_orphans = [
            o
            for o in managed_orders
            if str(o.get("symbol") or "").upper() not in active_pos_symbols
            and (
                o.get("reduceOnly") is True
                or str(o.get("rawType") or o.get("type") or "").upper()
                in ("STOP_MARKET", "TAKE_PROFIT_MARKET")
            )
        ]
        if reduce_only_orphans:
            orphan_symbols = sorted(
                {
                    str(o.get("symbol") or "").upper()
                    for o in reduce_only_orphans
                    if str(o.get("symbol") or "").strip()
                }
            )
            if not self.dry_run:
                for sym in orphan_symbols:
                    has_foreign_on_sym = any(
                        str(o.get("symbol") or "").upper() == sym
                        and not self._is_managed_order(o)
                        for o in scoped_orders
                    )
                    if has_foreign_on_sym:
                        if not is_vst_sandbox_client(self.client):
                            raise RuntimeError(
                                "Refusing to cancel orders: client is not verified in BingX VST sandbox mode or REAL_TRADING is enabled."
                            )
                        for orphan in reduce_only_orphans:
                            if str(orphan.get("symbol") or "").upper() == sym and orphan.get("id"):
                                ccxt_sym = orphan.get("ccxt_symbol") or to_ccxt_swap_symbol(sym)
                                await asyncio.to_thread(
                                    self.client.cancel_order, str(orphan["id"]), ccxt_sym
                                )
                    else:
                        await asyncio.to_thread(cancel_vst_open_orders, self.client, sym)
            actions_taken.append(f"CANCELLED_ORPHANS:{len(reduce_only_orphans)}")

        # 2. Check open scoped positions for missing SL + stale feed or imminent liquidation
        now_ts = time.time()
        feed_stale = (now_ts - self._last_feed_ts) > self.stale_feed_limit_sec

        for pos in positions:
            sym = str(pos.get("symbol") or "").upper()
            side = str(pos.get("positionSide") or pos.get("side") or "").upper()
            mark = float(pos.get("mark_price") or pos.get("entry_price") or 0.0)
            liq_p = pos.get("liquidation_price")
            qty = float(pos.get("contracts") or 0.0)

            sym_orders = [
                o for o in scoped_orders if str(o.get("symbol") or "").upper() == sym
            ]
            has_sl = any(
                "STOP" in str(o.get("rawType") or o.get("type") or "").upper()
                and "PROFIT" not in str(o.get("rawType") or o.get("type") or "").upper()
                for o in sym_orders
            )
            near_liq = (
                liq_p is not None
                and float(liq_p) > 0
                and mark > 0
                and abs(mark - float(liq_p)) / mark * 100.0 <= self.max_liq_distance_pct
            )

            if near_liq or (not has_sl and feed_stale):
                close_action = "CLOSE_LONG" if "LONG" in side else "CLOSE_SHORT"
                dec = TradeDecision.model_validate(
                    {
                        "decision_id": f"prot-emerg-{sym.lower()}-{int(now_ts)}-{uuid.uuid4().hex[:6]}",
                        "symbol": sym,
                        "action": close_action,
                        "order_type": "MARKET",
                        "qty": qty,
                        "reason": (
                            f"PositionProtectionWatcher emergency close "
                            f"(near_liq={near_liq}, has_sl={has_sl}, stale={feed_stale})"
                        ),
                    }
                )
                if not self.dry_run:
                    await asyncio.to_thread(
                        self.bridge.submit_decision,
                        dec,
                        current_price=mark,
                        open_positions_count=len(positions),
                        position_qty=qty,
                    )
                actions_taken.append(f"EMERGENCY_{close_action}:{sym}")
                continue

            if not (self.enable_breakeven_sl and qty > 0 and mark > 0):
                continue
            entry = float(pos.get("entry_price") or pos.get("entryPrice") or 0.0)
            if entry <= 0:
                continue
            is_long = "LONG" in side
            is_short = "SHORT" in side
            if not (is_long or is_short):
                continue
            pos_side = "LONG" if is_long else "SHORT"
            fav_move_pct = (
                (mark - entry) / entry * 100.0
                if is_long
                else (entry - mark) / entry * 100.0
            )
            if fav_move_pct < self.breakeven_trigger_pct:
                continue

            target_be_sl = (
                round(entry * (1.0 + self.breakeven_offset_pct / 100.0), 6)
                if is_long
                else round(entry * (1.0 - self.breakeven_offset_pct / 100.0), 6)
            )
            # Ensure target breakeven stop stays strictly behind current mark price
            if (is_long and target_be_sl >= mark) or (is_short and target_be_sl <= mark):
                continue

            managed_sl_orders = [
                o
                for o in sym_orders
                if self._is_managed_order(o)
                and "STOP" in str(o.get("rawType") or o.get("type") or "").upper()
                and "PROFIT" not in str(o.get("rawType") or o.get("type") or "").upper()
                and (
                    not str(o.get("positionSide") or "").strip()
                    or str(o.get("positionSide") or "").upper() == pos_side
                )
            ]
            # If foreign/unmanaged SL exists without any managed SL, do not overwrite foreign orders
            if has_sl and not managed_sl_orders:
                continue

            old_sl_id: str | None = None
            existing_sl_price: float | None = None
            for sl_ord in managed_sl_orders:
                p_raw = sl_ord.get("triggerPrice") or sl_ord.get("stopPrice")
                if p_raw is None:
                    continue
                try:
                    p_val = float(p_raw)
                except (TypeError, ValueError):
                    continue
                if p_val <= 0:
                    continue
                if existing_sl_price is None or (
                    (is_long and p_val > existing_sl_price)
                    or (is_short and p_val < existing_sl_price)
                ):
                    existing_sl_price = p_val
                    old_sl_id = str(sl_ord["id"]) if sl_ord.get("id") is not None else None

            # Never loosen SL: only move strictly closer to profit / above entry (LONG) or below (SHORT)
            if existing_sl_price is not None:
                if is_long and target_be_sl <= existing_sl_price * (1.0 + 1e-6):
                    continue
                if is_short and target_be_sl >= existing_sl_price * (1.0 - 1e-6):
                    continue

            if self.dry_run:
                actions_taken.append(f"DRY_WOULD_MOVE_BE_SL:{sym}->{target_be_sl}")
            else:
                await asyncio.to_thread(
                    move_vst_stop_loss,
                    self.client,
                    symbol=sym,
                    position_side=pos_side,
                    contracts=qty,
                    new_stop_price=target_be_sl,
                    old_order_id=old_sl_id,
                    decision_id=f"{int(now_ts)}",
                )
                actions_taken.append(f"MOVED_BE_SL:{sym}->{target_be_sl}")

        return {
            "dry_run": self.dry_run,
            "allowed_symbols": sorted(self.allowed_symbols),
            "open_positions": len(positions),
            "open_orders": len(managed_orders),
            "ignored_foreign_positions": ignored_foreign_positions,
            "ignored_foreign_orders": ignored_foreign_orders,
            "feed_stale": feed_stale,
            "actions": actions_taken,
        }


__all__ = ["PositionProtectionWatcher"]
