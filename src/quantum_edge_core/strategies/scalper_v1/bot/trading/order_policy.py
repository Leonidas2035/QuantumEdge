"""Order policy helper for scalp-mode execution.

This module does not introduce real limit-order book handling yet. Instead it
encapsulates how we *would* place orders and keeps logging transparent. The
actual executor still uses the existing trader.process API to avoid breaking
current flows.
"""

from __future__ import annotations

import asyncio
import json
import logging
from decimal import Decimal
from typing import Any, Dict, Optional

from quantumedge.execution.policies import Market, OrderSide
from quantumedge.execution.smart_executor import SmartMakerExecutor
from quantumedge.execution.types import (BookState, ExecutionReport,
                                         OrderRequest, SmartMakerConfig)

from quantum_edge_core.strategies.scalper_v1.bot.trading.smart_executor_adapter import \
    TraderExecutionAdapter


class OrderPolicy:
    """Encapsulate scalp order preferences (limit vs market, offsets, cancels)."""

    def __init__(
        self, settings: Dict[str, Any], logger: Optional[logging.Logger] = None
    ):
        self.settings = settings or {}
        self.logger = logger or logging.getLogger(__name__)

        policy = self.settings
        self.prefer_limit = bool(policy.get("prefer_limit", True))
        self.post_only = bool(policy.get("post_only", False))
        self.near_touch_offset_bps = float(policy.get("near_touch_offset_bps", 1.0))
        self.cancel_timeout_ms = int(policy.get("cancel_timeout_ms", 1500))
        self.max_partial_fill_time_ms = int(
            policy.get("max_partial_fill_time_ms", 2000)
        )
        self.min_fill_ratio_before_cancel = float(
            policy.get("min_fill_ratio_before_cancel", 0.25)
        )
        smart_cfg = self.settings.get("smart_executor", {}) or {}
        self.smart_enabled = bool(smart_cfg.get("enabled", False))
        self.smart_require_book = bool(smart_cfg.get("require_book", True))
        market_raw = str(smart_cfg.get("market", "usdm")).lower()
        try:
            self.smart_market = Market(market_raw)
        except ValueError:
            self.smart_market = Market.USDM
        self.smart_config = SmartMakerConfig.from_dict(smart_cfg)
        self._book_cache: Dict[str, BookState] = {}
        self._report_queues: Dict[str, asyncio.Queue[ExecutionReport]] = {}

    def update_book(
        self,
        symbol: str,
        bid_px: float,
        bid_qty: float,
        ask_px: float,
        ask_qty: float,
        ts_ms: int,
        tick_size: Optional[float] = None,
    ) -> None:
        try:
            book = BookState(
                symbol=str(symbol).upper(),
                best_bid_px=Decimal(str(bid_px)),
                best_bid_qty=Decimal(str(bid_qty)),
                best_ask_px=Decimal(str(ask_px)),
                best_ask_qty=Decimal(str(ask_qty)),
                ts_ms=int(ts_ms),
                tick_size=Decimal(str(tick_size)) if tick_size is not None else None,
            )
        except Exception:
            return
        self._book_cache[book.symbol] = book

    def push_execution_report(self, symbol: str, report: ExecutionReport) -> None:
        key = str(symbol).upper()
        queue = self._report_queues.get(key)
        if not queue:
            queue = asyncio.Queue()
            self._report_queues[key] = queue
        try:
            queue.put_nowait(report)
        except Exception:
            return

    async def place_scalp_order(
        self,
        trader,
        side: str,
        size: float,
        price: float,
        timestamp: int,
        symbol: str,
        tp_price: float = None,
        sl_price: float = None,
        client_order_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Place a scalp entry/exit.

        For now we map to the existing trader.process call (MARKET-equivalent)
        while keeping placeholders for limit/partial-fill handling.
        """
        order_type = "limit" if self.prefer_limit else "market"
        if self.smart_enabled:
            symbol_key = str(symbol).upper()
            book = self._book_cache.get(symbol_key)
            if not book:
                if self.smart_require_book:
                    return {"executed": False, "reason": "no_book"}
                self.logger.warning(
                    "Smart executor missing book for %s; falling back to legacy flow.",
                    symbol,
                )
            else:
                adapter = TraderExecutionAdapter(trader, self.smart_market, self.logger)
                executor = SmartMakerExecutor(
                    adapter,
                    self.smart_config,
                    logger=self.logger,
                    event_sink=lambda evt: self.logger.info(
                        "smart_exec=%s", json.dumps(evt, separators=(",", ":"))
                    ),
                )
                request = OrderRequest(
                    symbol=symbol_key,
                    side=OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL,
                    quantity=Decimal(str(size)),
                    signal_price=Decimal(str(price)),
                    market=self.smart_market,
                    client_order_id=client_order_id,
                )
                reports = self._report_queues.get(symbol_key)
                result = await executor.execute(
                    request,
                    book_provider=lambda: self._book_cache.get(symbol_key),
                    reports=reports,
                )
                return {
                    "executed": result.filled_qty > 0 or result.status == "done",
                    "status": result.status,
                    "reason": result.reason,
                    "filled_qty": float(result.filled_qty),
                    "remaining_qty": float(result.remaining_qty),
                }
        if self.prefer_limit:
            self.logger.debug(
                "Placing near-touch %s limit (post_only=%s offset_bps=%.3f) for %s size=%.4f",
                side,
                self.post_only,
                self.near_touch_offset_bps,
                symbol,
                size,
            )
        decision_obj = type(
            "TmpDecision",
            (),
            {
                "action": side,
                "size": size,
                "order_type": order_type,
                "tp_price": tp_price,
                "sl_price": sl_price,
            },
        )
        await trader.process(decision_obj, price, timestamp, symbol=symbol)
        return {
            "filled": True,
            "order_type": order_type,
            "size": size,
            "executed": True,
        }

    async def close_position(
        self, trader, size: float, price: float, timestamp: int, symbol: str
    ) -> Dict[str, Any]:
        """Close an open position using the existing executor API."""
        decision_obj = type(
            "TmpDecision",
            (),
            {"action": "close", "size": size, "order_type": "market"},
        )
        await trader.process(decision_obj, price, timestamp, symbol=symbol)
        return {"closed": True, "size": size}
