from bot.ops.metrics import MetricsTracker


def test_metrics_snapshot_contains_fields():
    tracker = MetricsTracker()
    tracker.record_reject("DATA_STALE")
    snapshot = tracker.snapshot({"symbol": "BTCUSDT"})
    assert "counters" in snapshot
    assert snapshot["counters"].get("reject:DATA_STALE") == 1
