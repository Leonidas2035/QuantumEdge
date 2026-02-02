import json
from pathlib import Path
from unittest.mock import MagicMock

from market_data.account.account_state import AccountState
from market_data.account.rest_snapshot import BinanceAccountRestSnapshotBuilder
from market_data.config import AccountConfig

FIXTURES_ROOT = Path("tests/fixtures")


class DummyResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


def _load_account_rest(name: str):
    path = FIXTURES_ROOT / "account_rest" / name
    return json.loads(path.read_text())


def _make_rest_session(responses):
    session = MagicMock()
    session.get.side_effect = [DummyResponse(payload) for payload in responses]
    return session


def _build_rest_builder():
    config = AccountConfig(spot_api_key="spot", spot_api_secret="secret", usdm_api_key="u", usdm_api_secret="s")
    responses = [
        _load_account_rest("ticker_price.json"),
        _load_account_rest("premium_index.json"),
        _load_account_rest("spot_account.json"),
        _load_account_rest("spot_open_orders.json"),
        _load_account_rest("usdm_account.json"),
        _load_account_rest("usdm_position_risk.json"),
        _load_account_rest("usdm_open_orders.json"),
    ]
    session = _make_rest_session(responses)
    return BinanceAccountRestSnapshotBuilder(config, session=session), config


def _load_ws_event(name: str) -> dict:
    path = FIXTURES_ROOT / "account_ws" / name
    return json.loads(path.read_text())


def test_spot_execution_report_manages_open_orders():
    state = AccountState(AccountConfig())
    new = _load_ws_event("spot_execution_report_new.json")
    delta_new = state.apply_spot_execution_report(new)
    assert delta_new is not None
    assert "123456" in state.spot_open_orders
    assert state.spot_open_orders["123456"].status == "NEW"
    filled = _load_ws_event("spot_execution_report_filled.json")
    delta_filled = state.apply_spot_execution_report(filled)
    assert delta_filled is not None
    assert "123456" not in state.spot_open_orders
    assert delta_filled.patch.spot.orders_update[0].status == "FILLED"


def test_usdm_account_update_populates_cache():
    state = AccountState(AccountConfig())
    delta = state.apply_usdm_ACCOUNT_UPDATE(_load_ws_event("usdm_account_update.json"))
    assert delta is not None
    assert state.usdm_account_totals is not None
    assert state.usdm_account_totals.totalMarginBalance == "2.1234"
    assert state.usdm_assets["BTC"].walletBalance == "0.050"
    assert state.usdm_positions[("BTCUSDT", "LONG")].leverage == "10"


def test_usdm_order_trade_update_manages_orders():
    state = AccountState(AccountConfig())
    new = _load_ws_event("usdm_order_trade_update_new.json")
    delta_new = state.apply_usdm_ORDER_TRADE_UPDATE(new)
    assert delta_new is not None
    assert "200001" in state.usdm_open_orders
    filled = _load_ws_event("usdm_order_trade_update_filled.json")
    delta_filled = state.apply_usdm_ORDER_TRADE_UPDATE(filled)
    assert delta_filled is not None
    assert "200001" not in state.usdm_open_orders
    assert delta_filled.patch.usdm.orders_update[0].status == "FILLED"


def test_snapshot_overwrites_cache():
    builder, config = _build_rest_builder()
    state = AccountState(config, rest_builder=builder)
    snapshot = state.build_snapshot(["BTCUSDT"])
    state.spot_balances["BTC"]["free"] = "0"
    state.apply_snapshot(snapshot)
    assert state.spot_balances["BTC"]["free"] == snapshot.spot.balances[0].free


def test_delta_fields_are_strings():
    state = AccountState(AccountConfig())
    event = _load_ws_event("spot_execution_report_new.json")
    event["o"]["price"] = 42000
    delta = state.apply_spot_execution_report(event)
    assert delta is not None
    assert isinstance(delta.patch.spot.orders_update[0].price, str)


def test_offline_smoke_sequence_generates_deltas():
    builder, config = _build_rest_builder()
    state = AccountState(config, rest_builder=builder)
    snapshot = state.build_snapshot(["BTCUSDT"])
    assert snapshot is not None
    events = [
        state.apply_spot_outboundAccountPosition(_load_ws_event("spot_outbound_position.json")),
        state.apply_spot_execution_report(_load_ws_event("spot_execution_report_new.json")),
        state.apply_usdm_ACCOUNT_UPDATE(_load_ws_event("usdm_account_update.json")),
        state.apply_usdm_ORDER_TRADE_UPDATE(_load_ws_event("usdm_order_trade_update_new.json")),
    ]
    assert all(event is not None for event in events)
    assert len(events) == 4
