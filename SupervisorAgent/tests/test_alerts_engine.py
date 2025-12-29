import time

from supervisor.alerts.engine import AlertEngine
from supervisor.alerts.rules import AlertRule
from supervisor.alerts.storage import AlertStorage


def test_alert_cooldown(tmp_path):
    storage = AlertStorage(tmp_path)
    rules = [
        AlertRule(
            name="latency_spike",
            severity="WARN",
            field="summary.latency_p95_ms",
            operator=">=",
            threshold=100,
            duration_sec=0,
            cooldown_sec=300,
        )
    ]
    engine = AlertEngine(rules, storage)
    summary = {"summary": {"latency_p95_ms": 200}}

    result = engine.evaluate(summary)
    assert len(result.active) == 1
    history = storage.recent_history(limit=10)
    assert len([item for item in history if item.get("type") == "ALERT_RAISED"]) == 1

    time.sleep(0.01)
    engine.evaluate(summary)
    history_after = storage.recent_history(limit=10)
    assert len([item for item in history_after if item.get("type") == "ALERT_RAISED"]) == 1
