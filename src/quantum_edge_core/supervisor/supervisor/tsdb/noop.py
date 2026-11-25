"""No-op TSDB store used when TSDB is disabled."""

from __future__ import annotations

from quantum_edge_core.supervisor.supervisor.tsdb.base import TimeseriesStore, Point


class NoopTimeseriesStore(TimeseriesStore):
    """Do nothing TSDB implementation."""

    def write_points(self, points: list[Point]) -> None:
        return

    def flush(self) -> None:
        return
