from pathlib import Path

from bot.state.store import StateStore, OrderRecord


def test_state_store_idempotency_and_persistence(tmp_path: Path):
    store = StateStore(tmp_path)
    oid = store.generate_client_order_id("BTCUSDT", "BUY", "buy", 1000, 0.5)
    oid2 = store.generate_client_order_id("BTCUSDT", "BUY", "buy", 1000, 0.5)
    assert oid == oid2

    record = OrderRecord(
        client_order_id=oid,
        symbol="BTCUSDT",
        side="BUY",
        size=0.5,
        price=30000.0,
        status="sent",
        created_ts=1000,
    )
    store.record_order(record)
    assert store.is_duplicate(oid)

    store.save_position_state(
        {"symbol": "BTCUSDT", "position": 1.0, "entry_price": 30000.0}
    )
    assert (tmp_path / "position_state.json").exists()
