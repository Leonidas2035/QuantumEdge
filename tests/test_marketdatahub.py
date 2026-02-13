import pytest

from market_data.bus.event_bus import EventBus
from market_data.models import L1Event, Priority, TradeEvent, encode_event, decode_event


def test_msgpack_roundtrip_trade_event() -> None:
    event = TradeEvent(
        ts_ns=1,
        symbol="BTCUSDT",
        event_type="trade",
        seq=1,
        priority=Priority.L0,
        price=42000.5,
        size=0.25,
        taker_side="buy",
    )
    packed = encode_event(event)
    decoded = decode_event(packed, TradeEvent)
    assert decoded.symbol == event.symbol
    assert decoded.price == pytest.approx(event.price)
    assert decoded.taker_side == "buy"


@pytest.mark.asyncio
async def test_event_bus_priority_sequence_gap() -> None:
    bus = EventBus(l0_hwm=1, l1_hwm=1, l2_hwm=1)
    symbol = "BTCUSDT"
    evt1 = TradeEvent(
        ts_ns=1,
        symbol=symbol,
        event_type="trade",
        seq=bus.assign_sequence(symbol, "trade"),
        priority=Priority.L0,
        price=1.0,
        size=1.0,
        taker_side="sell",
    )
    evt2 = L1Event(
        ts_ns=2,
        symbol=symbol,
        event_type="l1",
        seq=bus.assign_sequence(symbol, "l1"),
        priority=Priority.L1,
        best_bid=1.0,
        best_ask=2.0,
        bid_size=3.0,
        ask_size=4.0,
    )
    await bus.publish(evt1)
    await bus.publish(evt2)
    # L1 should be returned before L0 because of priority order default (L2->L1->L0)
    got1 = await bus.get_event()
    got2 = await bus.get_event()
    assert isinstance(got1, L1Event)
    assert isinstance(got2, TradeEvent)
    assert bus.last_sequence[(symbol, "trade")] == 1
    assert bus.last_sequence[(symbol, "l1")] == 1
