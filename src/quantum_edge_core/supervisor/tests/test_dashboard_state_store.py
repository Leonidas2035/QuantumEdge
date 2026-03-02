from pathlib import Path

from quantum_edge_core.supervisor.supervisor.alerts.engine import AlertEngine
from quantum_edge_core.supervisor.supervisor.alerts.storage import AlertStorage
from quantum_edge_core.supervisor.supervisor.dashboard.audit_log import DashboardAuditLogger
from quantum_edge_core.supervisor.supervisor.dashboard.state_store import DashboardStateStore


def _make_store(tmp_path: Path) -> DashboardStateStore:
    audit = DashboardAuditLogger(tmp_path / "audit.jsonl")
    alerts = AlertEngine([], AlertStorage(tmp_path / "alerts"))
    return DashboardStateStore(
        audit_logger=audit,
        alert_engine=alerts,
        telemetry_stale_ms=5000,
        cancel_window_sec=60,
        cancel_storm_threshold=20,
        dca_stuck_sell_ms=60000,
        alert_eval_interval_sec=1,
    )


def test_merge_limits_precedence(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.ingest_event(
        {
            "type": "strategy_limits.v1",
            "data": {
                "strategy_id": "SCALP",
                "symbol": "BTCUSDT",
                "max_position_notional": 1000,
                "allow_entries": True,
            },
        }
    )
    store.ingest_event(
        {
            "type": "regime_directive.v1",
            "data": {
                "strategy_id": "SCALP",
                "symbol": "BTCUSDT",
                "allow_entries": False,
                "mode": "risk_off",
            },
        }
    )
    strategies = store.strategies()
    assert strategies[0]["effective_limits"]["allow_entries"] is False
    assert strategies[0]["effective_limits"]["mode"] == "risk_off"


def test_dca_flash_visible_only_for_dca(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.ingest_event(
        {
            "type": "dca_flash_state.v1",
            "data": {"strategy_id": "DCA_ETH", "symbol": "ETHUSDT", "state": "flash"},
        }
    )
    store.ingest_event(
        {
            "type": "dca_flash_state.v1",
            "data": {"strategy_id": "SCALP", "symbol": "BTCUSDT", "state": "flash"},
        }
    )
    strategies = {item["strategy_id"]: item for item in store.strategies()}
    assert strategies["DCA_ETH"]["dca_flash"]["state"] == "flash"
    assert strategies["SCALP"]["dca_flash"] is None


def test_performance_and_reset(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.ingest_event(
        {
            "type": "dca_deal_closed.v1",
            "data": {
                "strategy_id": "DCA_ETH",
                "symbol": "ETHUSDT",
                "deal_id": "d1",
                "net_pnl": 5,
                "fees": 1,
                "volume_quote": 100,
            },
        }
    )
    store.ingest_event(
        {
            "type": "scalp_deal_closed.v1",
            "data": {
                "strategy_id": "SCALP",
                "symbol": "BTCUSDT",
                "deal_id": "s1",
                "net_pnl": -2,
                "fees": 0.5,
                "volume_quote": 50,
            },
        }
    )
    perf = store.performance()
    assert perf["session"]["closed_deals"] == 2
    assert perf["session"]["wins"] == 1
    assert perf["session"]["losses"] == 1

    store.reset_counters()
    perf_after = store.performance()
    assert perf_after["session"]["closed_deals"] == 0
    audit_items = store.audit(None, 50)["items"]
    assert any(item.get("event_type") == "counters_reset" for item in audit_items)
