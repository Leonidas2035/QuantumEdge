import json
from pathlib import Path

from quantum_edge_core.supervisor.supervisor.autopilot.collector import MetricsCollector


def test_metrics_collector_missing_returns_unknown(tmp_path: Path):
    collector = MetricsCollector(metrics_url="", metrics_path=tmp_path / "missing.json")
    snapshot = collector.collect()
    assert snapshot.health.status == "UNKNOWN"


def test_metrics_collector_reads_file(tmp_path: Path):
    path = tmp_path / "metrics.json"
    path.write_text(json.dumps({"ts": 123, "counters": {"orders": 1}}), encoding="utf-8")
    collector = MetricsCollector(metrics_url="", metrics_path=path)
    snapshot = collector.collect()
    assert snapshot.counters.get("orders") == 1
