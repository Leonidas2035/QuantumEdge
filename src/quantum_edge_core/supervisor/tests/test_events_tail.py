import json
from datetime import datetime, timezone
from pathlib import Path

from quantum_edge_core.supervisor.supervisor.events import BaseEvent, EventType, tail_events


def _write_events(path: Path) -> None:
    events = [
        {
            "ts": "2025-01-01T00:00:00+00:00",
            "type": "BOT_START",
            "source": "ProcessManager",
            "data": {"pid": 1},
        },
        BaseEvent(
            ts=datetime(2025, 1, 1, 0, 0, 1, tzinfo=timezone.utc),
            type=EventType.ORDER_DECISION,
            source="RiskEngine",
            data={"allowed": True},
        ).to_dict(),
    ]
    with path.open("w", encoding="utf-8") as handle:
        for event in events:
            json.dump(event, handle)
            handle.write("\n")


def test_tail_events_filters(tmp_path: Path) -> None:
    events_path = tmp_path / "events_2025-01-01.jsonl"
    _write_events(events_path)
    out = tail_events(events_path, limit=1, types=["ORDER_DECISION"])
    assert len(out) == 1
    assert out[0]["event_type"] == "ORDER_DECISION"
