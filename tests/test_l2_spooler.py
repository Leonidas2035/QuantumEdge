import gzip
import json
from pathlib import Path

from MarketDataHub.config import L2Config
from MarketDataHub.models import L2Envelope
from MarketDataHub.spool.l2_spooler import L2Spooler


class MockClock:
    def __init__(self) -> None:
        self._now = 0.0

    def now(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


def _read_line(file_path: Path) -> dict:
    with gzip.open(file_path, "rb") as handle:
        data = handle.read().strip()
    return json.loads(data.decode("utf-8"))


def _make_event(seq: int = 1) -> L2Envelope:
    return L2Envelope(ts_ns=seq, entity="fills", schema_ver=1, payload={"seq": seq})


def test_spool_writes(tmp_path: Path) -> None:
    config = L2Config(spool_dir=str(tmp_path), rotate_mb=1)
    spooler = L2Spooler(config)
    try:
        spooler.append(_make_event())
    finally:
        spooler.close()
    files = list(tmp_path.rglob("*.jsonl.gz"))
    assert files, "No spool file created"
    payload = _read_line(files[0])
    assert payload["entity"] == "fills"


def test_rotation_by_size(tmp_path: Path) -> None:
    config = L2Config(spool_dir=str(tmp_path), rotate_mb=0)
    spooler = L2Spooler(config)
    try:
        for idx in range(3):
            spooler.append(_make_event(seq=idx + 1))
    finally:
        spooler.close()
    files = list(tmp_path.rglob("*.jsonl.gz"))
    assert len(files) >= 2


def test_rotation_by_hour(tmp_path: Path) -> None:
    clock = MockClock()
    config = L2Config(spool_dir=str(tmp_path), rotate_mb=100)
    spooler = L2Spooler(config, time_provider=clock.now)
    try:
        spooler.append(_make_event(1))
        clock.advance(3605)
        spooler.append(_make_event(2))
    finally:
        spooler.close()
    dirs = [p.parent for p in tmp_path.rglob("*.jsonl.gz")]
    hours = sorted({dir_path.name for dir_path in dirs})
    assert "00" in hours and "01" in hours
