"""Map Supervisor events to TSDB points."""

from __future__ import annotations

import json
from datetime import datetime
from typing import List

from supervisor.events import BaseEvent, EventType
from supervisor.tsdb.base import Point


def event_to_points(event: BaseEvent) -> List[Point]:
    """Return zero or more points derived from the event."""

    ts = event.ts
    meas = None
    tags = {"source": event.source}
    fields = {}

    if event.type == EventType.SUPERVISOR_SNAPSHOT:
        meas = "snapshot"
        fields = _flatten_fields(event.data)
    elif event.type == EventType.STRATEGY_UPDATE:
        meas = "strategy_update"
        fields = _flatten_fields(event.data)
    elif event.type == EventType.ORDER_DECISION:
        meas = "order_decision"
        fields = _flatten_fields(event.data)
    elif event.type == EventType.ORDER_RESULT:
        meas = "order_result"
        fields = _flatten_fields(event.data)
    elif event.type == EventType.RISK_LIMIT_BREACH:
        meas = "risk_breach"
        fields = _flatten_fields(event.data)
    elif event.type == EventType.LLM_ADVICE:
        meas = "llm_advice"
        fields = _flatten_fields(event.data)
    elif event.type == EventType.META_SUPERVISOR_RESULT:
        meas = "meta_supervisor"
        fields = _flatten_fields(event.data)

    if not meas:
        return []

    return [
        Point(
            measurement=meas,
            ts=ts if isinstance(ts, datetime) else datetime.fromisoformat(str(ts)),
            tags=tags,
            fields=fields,
        )
    ]


def _flatten_fields(data: dict) -> dict:
    flat = {}
    for k, v in data.items():
        if isinstance(v, (int, float, str, bool)) or v is None:
            flat[k] = v
        else:
            flat[k] = json.dumps(v)
    return flat
