from datetime import datetime, timezone

from hermes.supervisor.tsdb.base import Point
from hermes.supervisor.tsdb.writer import TsdbWriter


class DummyStore:
    def __init__(self):
        self.points = []

    def write_points(self, points):
        self.points.extend(points)

    def flush(self):
        return


def test_tsdb_writer_batches_points():
    store = DummyStore()
    writer = TsdbWriter(store, flush_interval_seconds=60, batch_size=2)
    p1 = Point(
        "qe_events", datetime.now(timezone.utc), {"symbol": "BTCUSDT"}, {"value": 1}
    )
    p2 = Point(
        "qe_events", datetime.now(timezone.utc), {"symbol": "BTCUSDT"}, {"value": 2}
    )
    writer.enqueue([p1])
    assert len(store.points) == 0
    writer.enqueue([p2])
    assert len(store.points) == 2
