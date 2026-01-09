"""Replay adapters for running the LockBot policy runner in simulated time."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import msgspec

from MarketDataHub.lockbot.schema import LockbotMarketEvent
from MarketDataHub.models.events import Priority
from supervisor.contracts.lockbot_control_v1 import build_command, validate_command
from supervisor.lockbot.policy_runner import LockbotPolicyRunner


class ReplayControlClient:
    def __init__(
        self,
        bus: Any,
        *,
        cmd_topic: str,
        ack_topic: str,
        status_topic: str,
        bot_id: str,
        symbol: str,
        ttl_ms: int,
        clock: Any,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._bus = bus
        self._cmd_topic = cmd_topic
        self._ack_topic = ack_topic
        self._status_topic = status_topic
        self._bot_id = bot_id
        self._symbol = symbol
        self._ttl_ms = ttl_ms
        self._clock = clock
        self._logger = logger or logging.getLogger(__name__)
        self._last_status: Optional[Dict[str, Any]] = None
        self._acks: Dict[str, Dict[str, Any]] = {}
        self._cmd_counter = 0
        self._bus.subscribe(self._ack_topic, self._on_ack)
        self._bus.subscribe(self._status_topic, self._on_status)

    def send_command(self, cmd: str, payload: Dict[str, Any], ttl_ms: Optional[int] = None) -> str:
        self._cmd_counter += 1
        cmd_id = f"replay-{self._cmd_counter}"
        command = build_command(
            bot_id=self._bot_id,
            symbol=self._symbol,
            cmd=cmd,
            payload=payload,
            ttl_ms=ttl_ms if ttl_ms is not None else self._ttl_ms,
            cmd_id=cmd_id,
            ts_cmd=int(getattr(self._clock, "now_ms", 0) or 0),
        )
        data = msgspec.structs.asdict(command)
        ok, reason = validate_command(data)
        if not ok:
            raise ValueError(f"Invalid command: {reason}")
        self._bus.publish(self._cmd_topic, data)
        self._logger.info("Replay cmd sent cmd_id=%s cmd=%s", command.cmd_id, cmd)
        return command.cmd_id

    def status(self) -> Optional[Dict[str, Any]]:
        return dict(self._last_status) if self._last_status else None

    def ack(self, cmd_id: str) -> Optional[Dict[str, Any]]:
        return self._acks.get(cmd_id)

    def _on_ack(self, _topic: str, ack: Any) -> None:
        if isinstance(ack, dict):
            payload = ack
        else:
            payload = msgspec.structs.asdict(ack)
        cmd_id = str(payload.get("cmd_id") or "")
        if not cmd_id:
            return
        self._acks[cmd_id] = payload

    def _on_status(self, _topic: str, status: Any) -> None:
        payload = msgspec.structs.asdict(status) if not isinstance(status, dict) else status
        self._last_status = payload


class PolicyReplayAdapter:
    def __init__(
        self,
        runner: LockbotPolicyRunner,
        bus: Any,
        *,
        market_topics: list[str],
        symbol: str,
    ) -> None:
        self._runner = runner
        self._bus = bus
        self._market_topics = set(market_topics)
        self._symbol = symbol
        self._bus.subscribe(f"{symbol}:", self._on_market)

    def _on_market(self, topic: str, event: Any) -> None:
        if topic not in self._market_topics:
            return
        payload = event if isinstance(event, dict) else msgspec.structs.asdict(event)
        try:
            lockbot_event = _to_lockbot_event(payload)
        except ValueError:
            return
        self._runner.ingest_market_event(lockbot_event)

    def tick(self, now_ms: int) -> Optional[Dict[str, Any]]:
        return self._runner.run_once(now_ms=now_ms)


def _to_lockbot_event(payload: Dict[str, Any]) -> LockbotMarketEvent:
    topic = str(payload.get("topic") or "")
    symbol = str(payload.get("symbol") or "")
    if not topic or not symbol:
        raise ValueError("missing topic/symbol")
    event_type = payload.get("event_type")
    if not event_type and ":" in topic:
        event_type = topic.split(":", 1)[1]
    event_type = str(event_type or "")
    if not event_type:
        raise ValueError("missing event_type")
    ts_event = int(payload.get("ts_event") or 0)
    ts_pub = int(payload.get("ts_pub") or ts_event)
    seq = int(payload.get("seq") or 0)
    return LockbotMarketEvent(
        ts_ns=ts_event * 1_000_000,
        symbol=symbol,
        event_type=event_type,
        seq=seq,
        priority=Priority.L1,
        schema=str(payload.get("schema") or "lockbot_md.v1"),
        topic=topic,
        ts_event=ts_event,
        ts_pub=ts_pub,
        source=str(payload.get("source") or "replay"),
        payload=payload.get("payload") if isinstance(payload.get("payload"), dict) else {},
    )
