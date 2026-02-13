import pytest

from quantum_edge_core.supervisor.supervisor.tsdb.query import build_timeseries_query


def test_timeseries_allowlist_blocks_unknown_metric():
    with pytest.raises(ValueError):
        build_timeseries_query("unknown_metric", "BTCUSDT", "2025-01-01T00:00:00Z", "2025-01-01T01:00:00Z", "10s")
