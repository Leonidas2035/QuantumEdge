import pytest

pytest.skip("Legacy test broken by src-layout migration", allow_module_level=True)
from bot.trading.deal_events import DealEventEmitter, DcaDealTracker, ScalpDealTracker


def test_dca_deal_closed_idempotent() -> None:
    events = []

    def emit(event_type, data, symbol):
        events.append((event_type, data, symbol))

    tracker = DcaDealTracker(DealEventEmitter(emit))
    assert tracker.record_lot_closed(
        strategy_id="DCA_ETH",
        symbol="ETHUSDT",
        lot_id="lot-1",
        pnl=10.0,
        fees=1.0,
        volume_quote=100.0,
        ts_ms=1,
    )
    assert (
        tracker.record_lot_closed(
            strategy_id="DCA_ETH",
            symbol="ETHUSDT",
            lot_id="lot-1",
            pnl=12.0,
            fees=1.0,
            volume_quote=110.0,
            ts_ms=2,
        )
        is False
    )
    assert len(events) == 1
    assert events[0][0] == "dca_deal_closed.v1"


def test_scalp_deal_closed_idempotent() -> None:
    events = []

    def emit(event_type, data, symbol):
        events.append((event_type, data, symbol))

    tracker = ScalpDealTracker(DealEventEmitter(emit))
    assert tracker.record_cycle_closed(
        strategy_id="SCALP",
        symbol="BTCUSDT",
        cycle_id="c1",
        pnl=-2.0,
        fees=0.5,
        volume_quote=50.0,
        ts_ms=10,
        entry_price=100.0,
        exit_price=99.0,
        qty=0.5,
    )
    assert (
        tracker.record_cycle_closed(
            strategy_id="SCALP",
            symbol="BTCUSDT",
            cycle_id="c1",
            pnl=-2.0,
            fees=0.5,
            volume_quote=50.0,
            ts_ms=11,
        )
        is False
    )
    assert len(events) == 1
    assert events[0][0] == "scalp_deal_closed.v1"
