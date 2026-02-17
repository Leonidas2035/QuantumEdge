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
    assert (
        len([item for item in history_after if item.get("type") == "ALERT_RAISED"]) == 1
    )


def test_alert_resolve_after(tmp_path):
    storage = AlertStorage(tmp_path)
    rules = [
        AlertRule(
            name="stale",
            severity="WARN",
            field="summary.tick_age_ms",
            operator=">=",
            threshold=10,
            duration_sec=0,
            cooldown_sec=1,
            resolve_after_sec=10,
        )
    ]
    engine = AlertEngine(rules, storage)
    summary = {"summary": {"tick_age_ms": 20}}
    start = time.time()
    result = engine.evaluate(summary, now=start)
    assert len(result.active) == 1
    clear = {"summary": {"tick_age_ms": 0}}
    result = engine.evaluate(clear, now=start + 5)
    assert len(result.active) == 1
    result = engine.evaluate(clear, now=start + 11)
    assert len(result.active) == 0
