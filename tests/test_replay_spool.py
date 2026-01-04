import gzip
import json
from pathlib import Path

import pytest

from MarketDataHub.models import L2Envelope, encode_l2
from tools import replay_spool


def _write_spool_file(path: Path, events: list[L2Envelope]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wb") as fh:
        for event in events:
            fh.write(encode_l2(event))
            fh.write(b"\n")


@pytest.fixture(autouse=True)
def no_logging(monkeypatch):
    monkeypatch.setenv("PYTHONWARNINGS", "ignore")


def test_replay_dry_run_updates_state(tmp_path):
    spool_dir = tmp_path / "spool" / "l2"
    events = [
        L2Envelope(ts_ns=1, entity="fills", payload={"order_id": "x"}),
        L2Envelope(ts_ns=2, entity="positions", payload={"symbol": "BTC"}),
        L2Envelope(ts_ns=3, entity="equity", payload={"equity": 1.0}),
    ]
    file_path = spool_dir / "2026-01-01" / "00" / "l2_test.jsonl.gz"
    _write_spool_file(file_path, events)
    state_file = spool_dir / ".replay_state.json"
    replay_spool.run_replay(
        spool_dir=spool_dir,
        state_file=state_file,
        quest_host="127.0.0.1",
        ilp_port=9009,
        batch_rows=2,
        flush_interval_ms=10,
        max_files=1,
        dry_run=True,
        from_date=None,
        to_date=None,
        verify_http=False,
        http_port=9000,
    )
    assert state_file.exists()
    data = json.loads(state_file.read_text())
    assert data["last_line"] == len(events)
    assert data["last_file"].endswith("l2_test.jsonl.gz")


def test_list_spool_files_filters_by_date(tmp_path):
    spool_dir = tmp_path / "spool" / "l2"
    (spool_dir / "2026-01-01" / "00").mkdir(parents=True)
    (spool_dir / "2026-01-02" / "01").mkdir(parents=True)
    path1 = spool_dir / "2026-01-01" / "00" / "a.jsonl.gz"
    path2 = spool_dir / "2026-01-02" / "01" / "b.jsonl.gz"
    path1.write_text("")
    path2.write_text("")
    files = replay_spool._list_spool_files(spool_dir, "2026-01-02", None)
    assert files == [path2]
