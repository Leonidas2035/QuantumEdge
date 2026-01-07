import asyncio
from decimal import Decimal

import pytest

from quantumedge.execution.policies import FallbackPolicy, Market, OrderPolicy, OrderSide, OrderState
from quantumedge.execution.smart_executor import OrderSession, SmartMakerExecutor
from quantumedge.execution.types import BookState, ExecutionReport, OrderAck, OrderPlacement, OrderRequest, SmartMakerConfig


class FakeClient:
    def __init__(self, fill_on_place: bool = False):
        self.placements = []
        self.cancels = []
        self._fill_on_place = fill_on_place

    async def place_order(self, placement: OrderPlacement) -> OrderAck:
        self.placements.append(placement)
        filled = placement.quantity if self._fill_on_place and placement.order_type == "MARKET" else None
        return OrderAck(order_id=f"oid-{len(self.placements)}", client_order_id=placement.client_order_id, status="NEW", filled_qty=filled)

    async def cancel_order(self, *, symbol: str, order_id: str | None = None, client_order_id: str | None = None) -> bool:
        self.cancels.append((symbol, order_id, client_order_id))
        return True


def _book(bid: str, ask: str, bid_qty: str = "1", ask_qty: str = "1", ts_ms: int = 0) -> BookState:
    return BookState(
        symbol="BTCUSDT",
        best_bid_px=Decimal(bid),
        best_bid_qty=Decimal(bid_qty),
        best_ask_px=Decimal(ask),
        best_ask_qty=Decimal(ask_qty),
        ts_ms=ts_ms,
        tick_size=Decimal("1"),
    )


def test_reprice_respects_interval_and_max():
    client = FakeClient()
    cfg = SmartMakerConfig(min_reprice_interval_ms=100, max_reprices=1, reprice_ticks=1)
    executor = SmartMakerExecutor(client, cfg, clock_ms=lambda: 1050)
    session = OrderSession(
        state=OrderState.LIVE,
        order_id="oid",
        client_order_id="cid",
        side=OrderSide.BUY,
        price=Decimal("100"),
        filled_qty=Decimal("0"),
        remaining_qty=Decimal("1"),
        avg_fill_price=None,
        fees=None,
        reprices=0,
        chase_count=0,
        last_mid=Decimal("100.5"),
        last_reprice_ts=1000,
        last_replace_ts=0,
        maker_start_ts=0,
    )
    book = _book("99", "101")
    assert executor._should_reprice(session, book) is False
    session.last_reprice_ts = 900
    assert executor._should_reprice(session, book) is True
    session.reprices = 1
    assert executor._should_reprice(session, book) is False


def test_partial_fill_reduces_remaining_qty():
    client = FakeClient()
    cfg = SmartMakerConfig()
    executor = SmartMakerExecutor(client, cfg)
    session = OrderSession(
        state=OrderState.LIVE,
        order_id="oid",
        client_order_id="cid",
        side=OrderSide.BUY,
        price=Decimal("100"),
        filled_qty=Decimal("0"),
        remaining_qty=Decimal("1"),
        avg_fill_price=None,
        fees=None,
        reprices=0,
        chase_count=0,
        last_mid=None,
        last_reprice_ts=0,
        last_replace_ts=0,
        maker_start_ts=0,
    )
    report = ExecutionReport(order_id="oid", client_order_id="cid", status="PARTIALLY_FILLED", filled_qty=Decimal("0.4"))
    executor._apply_report(session, report)
    assert session.remaining_qty == Decimal("0.6")


@pytest.mark.asyncio
async def test_slippage_guard_aborts():
    client = FakeClient()
    cfg = SmartMakerConfig(max_slippage_bps=Decimal("5"), order_policy=OrderPolicy.MAKER_ONLY, fallback_policy=FallbackPolicy.NONE)
    executor = SmartMakerExecutor(client, cfg)
    request = OrderRequest(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity=Decimal("1"),
        signal_price=Decimal("100"),
        market=Market.USDM,
    )
    book = _book("110", "111")
    result = await executor.execute(request, book_provider=lambda: book)
    assert result.status == "aborted"
    assert result.reason == "slippage_guard"


@pytest.mark.asyncio
async def test_maker_timeout_triggers_market_fallback():
    client = FakeClient(fill_on_place=True)
    cfg = SmartMakerConfig(
        maker_timeout_ms=0,
        order_policy=OrderPolicy.MAKER_FIRST,
        fallback_policy=FallbackPolicy.MARKET,
        max_lifetime_ms=500,
    )
    executor = SmartMakerExecutor(client, cfg)
    request = OrderRequest(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity=Decimal("1"),
        signal_price=Decimal("100"),
        market=Market.USDM,
    )
    book = _book("100", "101")
    result = await executor.execute(request, book_provider=lambda: book)
    assert result.status == "done"
    assert result.reason == "fallback_market"
    assert any(order.order_type == "MARKET" for order in client.placements)


@pytest.mark.asyncio
async def test_market_quality_guard_blocks():
    client = FakeClient()
    cfg = SmartMakerConfig(spread_max_bps=Decimal("1"))
    executor = SmartMakerExecutor(client, cfg)
    request = OrderRequest(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity=Decimal("1"),
        signal_price=Decimal("100"),
        market=Market.USDM,
    )
    book = _book("100", "105")
    result = await executor.execute(request, book_provider=lambda: book)
    assert result.status == "aborted"
    assert result.reason == "market_quality"
