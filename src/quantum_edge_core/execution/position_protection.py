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
from typing import Any, Sequence

from quantum_edge_core.contracts.lean import TradeDecision
from quantum_edge_core.execution.bingx_vst_adapter import (
    cancel_vst_open_orders,
    fetch_vst_status,
)
from quantum_edge_core.execution.harness_bridge import HarnessBridge

logger = logging.getLogger("quantum_edge_core.execution.position_protection")

DEFAULT_ALLOWED_SYMBOLS: tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")


class PositionProtectionWatcher:
    """Always-on 5-second deterministic position safety loop (0 LLM tokens, single writer)."""

    def __init__(
        self,
        exchange_client: Any,
        bridge: HarnessBridge,
        *,
        allowed_symbols: Sequence[str] = DEFAULT_ALLOWED_SYMBOLS,
        interval_sec: float = 5.0,
        max_liq_distance_pct: float = 2.0,
        stale_feed_limit_sec: float = 30.0,
        dry_run: bool = False,
    ) -> None:
        self.client = exchange_client
        self.bridge = bridge
        self.allowed_symbols: frozenset[str] = frozenset(
            str(s).strip().upper() for s in allowed_symbols if str(s).strip()
        )
        self.interval_sec = max(interval_sec, 1.0)
        self.max_liq_distance_pct = max_liq_distance_pct
        self.stale_feed_limit_sec = stale_feed_limit_sec
        self.dry_run = dry_run
        self._last_feed_ts = time.time()
        self._stop = asyncio.Event()

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
        open_orders = [
            o for o in all_orders if str(o.get("symbol") or "").upper() in self.allowed_symbols
        ]
        ignored_foreign_positions = len(all_positions) - len(positions)
        ignored_foreign_orders = len(all_orders) - len(open_orders)

        # 1. Clean up orphan protective orders when scoped account positions are FLAT
        if len(positions) == 0 and len(open_orders) > 0:
            reduce_only_orphans = [
                o
                for o in open_orders
                if o.get("reduceOnly") is True
                or str(o.get("rawType") or o.get("type") or "").upper()
                in ("STOP_MARKET", "TAKE_PROFIT_MARKET")
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
                o for o in open_orders if str(o.get("symbol") or "").upper() == sym
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

        return {
            "dry_run": self.dry_run,
            "allowed_symbols": sorted(self.allowed_symbols),
            "open_positions": len(positions),
            "open_orders": len(open_orders),
            "ignored_foreign_positions": ignored_foreign_positions,
            "ignored_foreign_orders": ignored_foreign_orders,
            "feed_stale": feed_stale,
            "actions": actions_taken,
        }


__all__ = ["PositionProtectionWatcher"]
