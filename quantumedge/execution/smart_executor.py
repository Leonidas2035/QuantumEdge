"""Maker-first smart executor with bounded cancel/replace and fallbacks."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Optional

from quantumedge.execution.policies import ExecutionStatus, FallbackPolicy, Market, OrderPolicy, OrderSide, OrderState
from quantumedge.execution.types import (
    BookProvider,
    BookState,
    ExecutionClient,
    ExecutionReport,
    ExecutionResult,
    OrderAck,
    OrderPlacement,
    OrderRequest,
    SmartMakerConfig,
)


@dataclass
class OrderSession:
    state: OrderState
    order_id: Optional[str]
    client_order_id: Optional[str]
    side: OrderSide
    price: Optional[Decimal]
    filled_qty: Decimal
    remaining_qty: Decimal
    avg_fill_price: Optional[Decimal]
    fees: Optional[Decimal]
    reprices: int
    chase_count: int
    last_mid: Optional[Decimal]
    last_reprice_ts: int
    last_replace_ts: int
    maker_start_ts: int


class SmartMakerExecutor:
    """Maker-first execution with post-only chasing and safe fallbacks."""

    def __init__(
        self,
        client: ExecutionClient,
        cfg: SmartMakerConfig,
        *,
        event_sink: Optional[Callable[[dict], None]] = None,
        logger: Optional[logging.Logger] = None,
        clock_ms: Optional[Callable[[], int]] = None,
        sleep_fn: Optional[Callable[[float], asyncio.Future]] = None,
    ) -> None:
        self._client = client
        self._cfg = cfg
        self._event_sink = event_sink
        self._logger = logger or logging.getLogger(__name__)
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self._sleep = sleep_fn or asyncio.sleep

    async def execute(
        self,
        request: OrderRequest,
        *,
        book_provider: BookProvider,
        reports: Optional[asyncio.Queue[ExecutionReport]] = None,
    ) -> ExecutionResult:
        start_ms = self._now_ms()
        session = OrderSession(
            state=OrderState.INIT,
            order_id=None,
            client_order_id=request.client_order_id,
            side=request.side,
            price=None,
            filled_qty=Decimal("0"),
            remaining_qty=request.quantity,
            avg_fill_price=None,
            fees=None,
            reprices=0,
            chase_count=0,
            last_mid=None,
            last_reprice_ts=0,
            last_replace_ts=0,
            maker_start_ts=start_ms,
        )
        book = book_provider()
        if not book:
            return self._finalize(session, start_ms, ExecutionStatus.ABORTED, "no_book")
        if not self._market_quality_ok(book):
            return self._finalize(session, start_ms, ExecutionStatus.ABORTED, "market_quality")
        if not self._slippage_ok(book, request.signal_price):
            return self._finalize(session, start_ms, ExecutionStatus.ABORTED, "slippage_guard")

        maker_price = self._maker_price(book, request.side)
        if maker_price is None:
            return self._finalize(session, start_ms, ExecutionStatus.ABORTED, "no_price")

        session.last_mid = self._mid_price(book)
        if self._below_minimums(session.remaining_qty, maker_price):
            return self._finalize(session, start_ms, ExecutionStatus.DONE, "min_remaining")

        ack = await self._place_maker(request, session, maker_price)
        if not ack:
            return self._finalize(session, start_ms, ExecutionStatus.FAILED, "place_failed")
        session = self._apply_ack(session, ack, maker_price)
        session.state = OrderState.LIVE
        self._emit_transition(session, "live")

        while session.state not in {OrderState.DONE, OrderState.ABORTED, OrderState.FAILED}:
            now_ms = self._now_ms()
            if self._cfg.max_lifetime_ms and now_ms - start_ms > self._cfg.max_lifetime_ms:
                await self._cancel_open(request, session, reason="lifetime_guard")
                return self._finalize(session, start_ms, ExecutionStatus.ABORTED, "timeout")

            await self._drain_reports(reports, session)
            if session.remaining_qty <= 0:
                return self._finalize(session, start_ms, ExecutionStatus.DONE, "filled")
            if session.state == OrderState.PARTIAL:
                if self._below_minimums(session.remaining_qty, session.price):
                    await self._cancel_open(request, session, reason="min_remaining")
                    return self._finalize(session, start_ms, ExecutionStatus.DONE, "min_remaining")

            book = book_provider()
            if book:
                session.last_mid = self._mid_price(book)
                if not self._slippage_ok(book, request.signal_price):
                    await self._cancel_open(request, session, reason="slippage_guard")
                    return self._finalize(session, start_ms, ExecutionStatus.ABORTED, "slippage_guard")
                if not self._market_quality_ok(book):
                    await self._cancel_open(request, session, reason="market_quality")
                    return self._finalize(session, start_ms, ExecutionStatus.ABORTED, "market_quality")

                if self._should_reprice(session, book):
                    if self._can_replace(now_ms, session):
                        await self._cancel_open(request, session, reason="reprice")
                        session.state = OrderState.REPRICING
                        self._emit_transition(session, "repricing")
                        new_price = self._maker_price(book, request.side)
                        if new_price is None:
                            return self._finalize(session, start_ms, ExecutionStatus.ABORTED, "no_price")
                        if self._below_minimums(session.remaining_qty, new_price):
                            return self._finalize(session, start_ms, ExecutionStatus.DONE, "min_remaining")
                        ack = await self._place_maker(request, session, new_price)
                        if not ack:
                            return self._finalize(session, start_ms, ExecutionStatus.FAILED, "place_failed")
                        session = self._apply_ack(session, ack, new_price)
                        session.reprices += 1
                        session.chase_count += 1
                        session.last_reprice_ts = now_ms
                        session.last_replace_ts = now_ms
                        session.state = OrderState.LIVE
                        self._emit_transition(session, "live")

            maker_timeout = now_ms - session.maker_start_ts >= self._cfg.maker_timeout_ms
            reprices_exhausted = session.reprices >= self._cfg.max_reprices
            if (maker_timeout or reprices_exhausted) and session.remaining_qty > 0:
                if self._cfg.order_policy == OrderPolicy.MAKER_ONLY:
                    await self._cancel_open(request, session, reason="maker_only")
                    return self._finalize(session, start_ms, ExecutionStatus.ABORTED, "maker_only")
                fallback = await self._apply_fallback(request, session, book, start_ms)
                if fallback:
                    return fallback
                await self._cancel_open(request, session, reason="fallback_failed")
                return self._finalize(session, start_ms, ExecutionStatus.ABORTED, "fallback_failed")

            await self._sleep(self._cfg.poll_interval_ms / 1000.0)

        return self._finalize(session, start_ms, ExecutionStatus.DONE, "done")

    def reconcile_order_state(self, reason: str) -> None:
        """Hook for REST snapshot reconciliation when order state is ambiguous."""
        self._logger.warning("Reconcile order state requested: %s", reason)

    def _now_ms(self) -> int:
        return int(self._clock_ms())

    def _emit_transition(self, session: OrderSession, transition: str, **extra: object) -> None:
        if not self._event_sink:
            return
        payload = {
            "transition": transition,
            "state": session.state.value,
            "order_id": session.order_id,
            "client_oid": session.client_order_id,
            "chase_count": session.chase_count,
            "reprices_count": session.reprices,
            "filled_qty": str(session.filled_qty),
            "avg_fill_price": str(session.avg_fill_price) if session.avg_fill_price else None,
            "fees": str(session.fees) if session.fees else None,
            **extra,
        }
        self._event_sink(payload)

    def _mid_price(self, book: BookState) -> Optional[Decimal]:
        if book.best_bid_px <= 0 or book.best_ask_px <= 0:
            return None
        return (book.best_bid_px + book.best_ask_px) / Decimal("2")

    def _spread_bps(self, book: BookState) -> Optional[Decimal]:
        mid = self._mid_price(book)
        if not mid or mid <= 0:
            return None
        spread = book.best_ask_px - book.best_bid_px
        if spread < 0:
            return None
        return spread / mid * Decimal("10000")

    def _maker_price(self, book: BookState, side: OrderSide) -> Optional[Decimal]:
        if side == OrderSide.BUY:
            return book.best_bid_px
        if side == OrderSide.SELL:
            return book.best_ask_px
        return None

    def _aggressive_price(self, book: BookState, side: OrderSide) -> Optional[Decimal]:
        offset_ticks = max(self._cfg.aggressive_limit_offset_ticks, 0)
        if offset_ticks == 0:
            offset = Decimal("0")
        elif book.tick_size:
            offset = book.tick_size * Decimal(offset_ticks)
        else:
            offset = Decimal(offset_ticks)
        if side == OrderSide.BUY:
            return book.best_ask_px + offset
        if side == OrderSide.SELL:
            return book.best_bid_px - offset
        return None

    def _slippage_ok(self, book: BookState, signal_price: Decimal) -> bool:
        mid = self._mid_price(book)
        if not mid:
            return False
        if self._cfg.max_slippage_abs and abs(mid - signal_price) > self._cfg.max_slippage_abs:
            return False
        if self._cfg.max_slippage_bps:
            if signal_price <= 0:
                return False
            bps = abs(mid - signal_price) / signal_price * Decimal("10000")
            if bps > self._cfg.max_slippage_bps:
                return False
        return True

    def _market_quality_ok(self, book: BookState) -> bool:
        if self._cfg.spread_max_bps is not None:
            spread_bps = self._spread_bps(book)
            if spread_bps is None or spread_bps > self._cfg.spread_max_bps:
                return False
        top_qty_sum = book.best_bid_qty + book.best_ask_qty
        if self._cfg.min_top_depth_qty is not None and top_qty_sum < self._cfg.min_top_depth_qty:
            return False
        if self._cfg.min_top_depth_usd is not None:
            mid = self._mid_price(book)
            if not mid:
                return False
            top_usd = mid * top_qty_sum
            if top_usd < self._cfg.min_top_depth_usd:
                return False
        return True

    def _below_minimums(self, qty: Decimal, price: Optional[Decimal]) -> bool:
        if self._cfg.min_remaining_qty is not None and qty < self._cfg.min_remaining_qty:
            return True
        if self._cfg.min_notional is not None and price is not None:
            notional = qty * price
            if notional < self._cfg.min_notional:
                return True
        return False

    async def _place_maker(self, request: OrderRequest, session: OrderSession, price: Decimal) -> Optional[OrderAck]:
        order_type = "LIMIT_MAKER" if request.market == Market.SPOT else "LIMIT"
        time_in_force = None if request.market == Market.SPOT else "GTX"
        placement = OrderPlacement(
            symbol=request.symbol,
            side=request.side,
            quantity=session.remaining_qty,
            price=price,
            order_type=order_type,
            time_in_force=time_in_force,
            post_only=True,
            client_order_id=session.client_order_id,
            reduce_only=request.reduce_only,
        )
        session.state = OrderState.PLACING
        self._emit_transition(session, "placing")
        return await self._client.place_order(placement)

    async def _place_aggressive(self, request: OrderRequest, session: OrderSession, book: BookState) -> Optional[OrderAck]:
        price = self._aggressive_price(book, request.side)
        if price is None:
            return None
        tif = "IOC" if request.market in {Market.SPOT, Market.USDM} else "GTC"
        placement = OrderPlacement(
            symbol=request.symbol,
            side=request.side,
            quantity=session.remaining_qty,
            price=price,
            order_type="LIMIT",
            time_in_force=tif,
            post_only=False,
            client_order_id=session.client_order_id,
            reduce_only=request.reduce_only,
        )
        self._emit_transition(session, "fallback_aggressive")
        return await self._client.place_order(placement)

    async def _place_market(self, request: OrderRequest, session: OrderSession) -> Optional[OrderAck]:
        placement = OrderPlacement(
            symbol=request.symbol,
            side=request.side,
            quantity=session.remaining_qty,
            price=None,
            order_type="MARKET",
            time_in_force=None,
            post_only=False,
            client_order_id=session.client_order_id,
            reduce_only=request.reduce_only,
        )
        self._emit_transition(session, "fallback_market")
        return await self._client.place_order(placement)

    async def _cancel_open(self, request: OrderRequest, session: OrderSession, *, reason: str) -> None:
        if not session.order_id and not session.client_order_id:
            return
        ok = await self._client.cancel_order(
            symbol=request.symbol,
            order_id=session.order_id,
            client_order_id=session.client_order_id,
        )
        session.state = OrderState.CANCELING
        self._emit_transition(session, "canceling", cancel_reason=reason)
        if not ok:
            self.reconcile_order_state("cancel_failed")

    def _should_reprice(self, session: OrderSession, book: BookState) -> bool:
        if session.price is None:
            return False
        if session.reprices >= self._cfg.max_reprices:
            return False
        if session.last_reprice_ts and self._now_ms() - session.last_reprice_ts < self._cfg.min_reprice_interval_ms:
            return False
        desired = self._maker_price(book, session.side)
        if desired is None:
            return False
        if desired != session.price:
            return True
        if session.last_mid and self._mid_price(book):
            tick_size = book.tick_size or Decimal("1")
            threshold = tick_size * Decimal(max(self._cfg.reprice_ticks, 0))
            if abs(self._mid_price(book) - session.last_mid) > threshold:
                return True
        return False

    def _can_replace(self, now_ms: int, session: OrderSession) -> bool:
        if not self._cfg.enable_cancel_replace_throttle:
            return True
        if session.last_replace_ts == 0:
            return True
        return now_ms - session.last_replace_ts >= self._cfg.throttle_ms

    async def _apply_fallback(
        self,
        request: OrderRequest,
        session: OrderSession,
        book: Optional[BookState],
        start_ms: int,
    ) -> Optional[ExecutionResult]:
        if self._cfg.fallback_policy == FallbackPolicy.NONE:
            await self._cancel_open(request, session, reason="fallback_none")
            return self._finalize(session, start_ms, ExecutionStatus.ABORTED, "fallback_none")
        if self._cfg.fallback_policy == FallbackPolicy.AGGRESSIVE_LIMIT and book:
            await self._cancel_open(request, session, reason="fallback_aggressive")
            ack = await self._place_aggressive(request, session, book)
            if not ack:
                return None
            session = self._apply_ack(session, ack, self._aggressive_price(book, request.side))
            return self._finalize(session, start_ms, ExecutionStatus.DONE, "fallback_aggressive")
        if self._cfg.fallback_policy == FallbackPolicy.MARKET:
            await self._cancel_open(request, session, reason="fallback_market")
            ack = await self._place_market(request, session)
            if not ack:
                return None
            session = self._apply_ack(session, ack, session.price)
            return self._finalize(session, start_ms, ExecutionStatus.DONE, "fallback_market")
        return None

    async def _drain_reports(self, reports: Optional[asyncio.Queue[ExecutionReport]], session: OrderSession) -> None:
        if not reports:
            return
        while True:
            try:
                report = reports.get_nowait()
            except asyncio.QueueEmpty:
                break
            session = self._apply_report(session, report)
            if report.status.upper() in {"FILLED", "CANCELED", "REJECTED"}:
                break

    def _apply_report(self, session: OrderSession, report: ExecutionReport) -> OrderSession:
        filled = report.filled_qty
        if filled > session.filled_qty:
            delta = filled - session.filled_qty
            session.filled_qty = filled
            session.remaining_qty = max(session.remaining_qty - delta, Decimal("0"))
        if report.avg_price is not None:
            session.avg_fill_price = report.avg_price
        if report.fee is not None:
            session.fees = (session.fees or Decimal("0")) + report.fee
        if report.status.upper() == "PARTIALLY_FILLED":
            session.state = OrderState.PARTIAL
        elif report.status.upper() == "FILLED":
            session.state = OrderState.DONE
        elif report.status.upper() in {"CANCELED", "REJECTED"}:
            session.state = OrderState.ABORTED
        return session

    def _apply_ack(self, session: OrderSession, ack: OrderAck, price: Optional[Decimal]) -> OrderSession:
        session.order_id = ack.order_id or session.order_id
        session.client_order_id = ack.client_order_id or session.client_order_id
        session.price = price
        if ack.filled_qty:
            session.filled_qty += ack.filled_qty
            session.remaining_qty = max(session.remaining_qty - ack.filled_qty, Decimal("0"))
        if ack.avg_price:
            session.avg_fill_price = ack.avg_price
        return session

    def _finalize(
        self,
        session: OrderSession,
        start_ms: int,
        status: ExecutionStatus,
        reason: str,
    ) -> ExecutionResult:
        lifetime_ms = max(self._now_ms() - start_ms, 0)
        time_to_fill = lifetime_ms if session.filled_qty > 0 else None
        effective_spread = None
        if session.price and session.last_mid and session.last_mid > 0:
            effective_spread = (session.price - session.last_mid) / session.last_mid * Decimal("10000")
        result = ExecutionResult(
            status=status.value,
            reason=reason,
            filled_qty=session.filled_qty,
            remaining_qty=session.remaining_qty,
            avg_fill_price=session.avg_fill_price,
            fees=session.fees,
            order_id=session.order_id,
            client_order_id=session.client_order_id,
            lifetime_ms=lifetime_ms,
            time_to_fill_ms=time_to_fill,
            reprices=session.reprices,
            chase_count=session.chase_count,
            effective_spread_bps=effective_spread,
        )
        self._emit_transition(
            session,
            "final",
            result_status=status.value,
            reason=reason,
            abort_reason=reason if status == ExecutionStatus.ABORTED else None,
            effective_spread_bps=str(effective_spread) if effective_spread else None,
            time_to_fill_ms=time_to_fill,
            lifetime_ms=lifetime_ms,
        )
        return result
