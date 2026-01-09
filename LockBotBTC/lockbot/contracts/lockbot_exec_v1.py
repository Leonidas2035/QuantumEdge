"""LockBot execution event contract."""

from __future__ import annotations

from typing import Any, Dict

import msgspec


SCHEMA_VERSION = "lockbot_exec.v1"

EXEC_TOPIC = "LOCKBOT:BTCUSDT:exec"

EVENT_TYPES = {
    "ORDER_SUBMITTED",
    "ORDER_ACKED",
    "ORDER_REJECTED",
    "ORDER_PARTIALLY_FILLED",
    "ORDER_FILLED",
    "ORDER_CANCELED",
    "RECONCILIATION_MISMATCH",
    "EXECUTION_DISABLED",
}


class ExecEnvelope(msgspec.Struct):
    schema: str
    msg_type: str
    bot_id: str
    symbol: str
    ts_event: int
    seq: int
    event_type: str
    payload: Dict[str, Any]
