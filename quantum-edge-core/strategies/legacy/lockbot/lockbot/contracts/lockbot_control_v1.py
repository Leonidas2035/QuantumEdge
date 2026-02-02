"""LockBot control plane contract (commands, acknowledgements, status)."""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, Tuple

import msgspec


SCHEMA_VERSION = "lockbot_control.v1"

CMD_TOPIC = "LOCKBOT:BTCUSDT:cmd"
ACK_TOPIC = "LOCKBOT:BTCUSDT:ack"
STATUS_TOPIC = "LOCKBOT:BTCUSDT:status"

CMD_TYPES = {
    "SET_REGIME",
    "SET_DELTA_TARGET",
    "EXEC_STEP",
    "PANIC_LOCK",
    "EXIT_LOCK",
    "PAUSE",
    "RESUME",
    "ARM_EXECUTION",
    "DISARM_EXECUTION",
    "CANCEL_ALL",
}

ACK_STATUS = {"ACCEPTED", "REJECTED", "IGNORED_DUPLICATE", "EXPIRED", "ERROR"}


class CommandEnvelope(msgspec.Struct):
    schema: str
    msg_type: str
    bot_id: str
    symbol: str
    cmd_id: str
    ts_cmd: int
    ttl_ms: int
    source: str
    payload: Dict[str, Any]


class AckEnvelope(msgspec.Struct):
    schema: str
    msg_type: str
    bot_id: str
    symbol: str
    cmd_id: str
    ts_ack: int
    payload: Dict[str, Any]


class StatusEnvelope(msgspec.Struct):
    schema: str
    msg_type: str
    bot_id: str
    symbol: str
    ts_event: int
    seq: int
    payload: Dict[str, Any]


def build_command(
    *,
    bot_id: str,
    symbol: str,
    cmd: str,
    payload: Dict[str, Any],
    ttl_ms: int,
    source: str = "SupervisorAgent",
    cmd_id: str | None = None,
    ts_cmd: int | None = None,
) -> CommandEnvelope:
    now_ms = int(time.time() * 1000)
    return CommandEnvelope(
        schema=SCHEMA_VERSION,
        msg_type="cmd",
        bot_id=bot_id,
        symbol=symbol,
        cmd_id=cmd_id or str(uuid.uuid4()),
        ts_cmd=ts_cmd if ts_cmd is not None else now_ms,
        ttl_ms=int(ttl_ms),
        source=source,
        payload={"cmd": cmd, **payload},
    )


def validate_command(command: Dict[str, Any]) -> Tuple[bool, str]:
    if command.get("schema") != SCHEMA_VERSION:
        return False, "schema"
    if command.get("msg_type") != "cmd":
        return False, "msg_type"
    if not command.get("bot_id") or not command.get("symbol"):
        return False, "identity"
    if not command.get("cmd_id"):
        return False, "cmd_id"
    payload = command.get("payload")
    if not isinstance(payload, dict):
        return False, "payload"
    cmd = payload.get("cmd")
    if cmd not in CMD_TYPES:
        return False, "cmd"
    if cmd == "SET_REGIME" and payload.get("regime") not in {"RANGE", "TREND_UP", "TREND_DOWN", "CHAOS"}:
        return False, "regime"
    if cmd == "SET_DELTA_TARGET":
        if payload.get("target") is None:
            return False, "target"
    if cmd == "EXEC_STEP" and payload.get("action") not in {"TRIM_LONG", "TRIM_SHORT", "ADD_LONG", "ADD_SHORT"}:
        return False, "action"
    if cmd == "PANIC_LOCK" and "force_1to1" not in payload:
        return False, "force_1to1"
    if cmd == "EXIT_LOCK" and payload.get("mode") not in {"MARKET", "LIMIT_AROUND_VWAP"}:
        return False, "exit_mode"
    if cmd == "ARM_EXECUTION":
        if payload.get("mode") not in {"DRY_RUN", "DEMO_TESTNET", "LIVE_MAINNET"}:
            return False, "mode"
        if payload.get("ttl_s") is None:
            return False, "ttl_s"
    if cmd == "CANCEL_ALL" and payload.get("scope") not in {"OPEN_ONLY", "ALL"}:
        return False, "scope"
    return True, ""
