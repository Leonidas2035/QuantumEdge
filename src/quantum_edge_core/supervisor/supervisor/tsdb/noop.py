"""No-op TSDB store used when TSDB is disabled."""

from __future__ import annotations

from supervisor.tsdb.base import Point, TimeseriesStore


class NoopTimeseriesStore(TimeseriesStore):
    """Do nothing TSDB implementation."""

    def write_points(self, points: list[Point]) -> None:
        return

    def flush(self) -> None:
        return
