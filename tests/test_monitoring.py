import time

from SupervisorAgent.monitoring.aggregator import TelemetryAggregator
from SupervisorAgent.monitoring.alerts import AlertManager
from SupervisorAgent.monitoring.api import normalize_event


def test_event_normalization_defaults():
    payload = {"ts": 1700000000000}
    event = normalize_event(payload)
    assert event["event_version"] == "telemetry.v1"
    assert event["type"] == "unknown"
    assert isinstance(event["ts"], int)
    assert event["ts"] < 2000000000  # ms -> sec


def test_aggregator_summary_counts():
    agg = TelemetryAggregator()
    now = int(time.time())
    for i in range(3):
        agg.process_event({"ts": now - i, "type": "order", "data": {}})
    agg.process_event({"ts": now, "type": "error", "data": {}})
    agg.process_event({"ts": now, "type": "latency", "data": {"loop_ms": 120}})
    summary = agg.summary().to_dict()
    assert summary["trades_5m"] == 3
    assert summary["error_rate_1m"] == 1
    assert summary["latency_ms_avg"] == 120


def test_alert_cooldown():
    manager = AlertManager({"error_rate_1m": 1}, cooldown_sec=300)
    summary = {"error_rate_1m": 2}
    manager.evaluate(summary)
    manager.evaluate(summary)
    recent = manager.recent_alerts()
    assert len(recent) == 1
