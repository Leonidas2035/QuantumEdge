"""Replay adapter for LockBotBTC service."""

from __future__ import annotations

from typing import Any, Dict, Optional

from LockBotBTC.lockbot.contracts.lockbot_control_v1 import ACK_TOPIC, CMD_TOPIC, STATUS_TOPIC
from LockBotBTC.lockbot_btc.main import LockBotService
from LockBotBTC.lockbot_btc.replay.bus import ReplayBus
from LockBotBTC.lockbot_btc.replay.clock import ReplayClock


class ReplayBotAdapter:
    def __init__(self, service: LockBotService, bus: ReplayBus, clock: ReplayClock) -> None:
        self._service = service
        self._bus = bus
        self._clock = clock
        self._bus.subscribe(CMD_TOPIC, self._on_cmd)

    def on_market_event(self, _topic: str, event: Dict[str, Any]) -> None:
        self._service.handle_market_event(event)

    def on_account_event(self, _topic: str, payload: Dict[str, Any]) -> None:
        self._service.handle_account_payload(payload)

    def emit_status(self, now_ms: Optional[int] = None) -> None:
        status = self._service.build_status(now_ms=now_ms or self._clock.now_ms)
        self._bus.publish(STATUS_TOPIC, status)

    def _on_cmd(self, _topic: str, command: Dict[str, Any]) -> None:
        now_ms = int(command.get("ts_cmd") or 0) or self._clock.now_ms
        ack = self._service.process_command(command, now_ms=now_ms)
        self._bus.publish(ACK_TOPIC, ack)
        status = self._service.build_status(now_ms=now_ms)
        self._bus.publish(STATUS_TOPIC, status)
