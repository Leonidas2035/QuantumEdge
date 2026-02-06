"""Supervisor control client for LockBotBTC."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Optional

import msgspec
import zmq

from supervisor.config import LockbotControlConfig
from supervisor.contracts.lockbot_control_v1 import (
    AckEnvelope,
    StatusEnvelope,
    build_command,
    validate_command,
)
from supervisor.contracts.lockbot_exec_v1 import ExecEnvelope


class LockbotControlClient:
    def __init__(self, cfg: LockbotControlConfig, logger: Optional[logging.Logger] = None) -> None:
        self._cfg = cfg
        self._logger = logger or logging.getLogger(__name__)
        self._ctx = zmq.Context.instance()
        self._pub = self._ctx.socket(zmq.PUB)
        self._pub.setsockopt(zmq.LINGER, 0)
        self._pub.bind(cfg.cmd_endpoint)
        self._sub = self._ctx.socket(zmq.SUB)
        self._sub.setsockopt(zmq.LINGER, 0)
        self._sub.setsockopt(zmq.RCVHWM, cfg.rcv_hwm)
        self._sub.setsockopt(zmq.SUBSCRIBE, cfg.ack_topic.encode("utf-8"))
        self._sub.setsockopt(zmq.SUBSCRIBE, cfg.status_topic.encode("utf-8"))
        self._sub.setsockopt(zmq.SUBSCRIBE, cfg.exec_topic.encode("utf-8"))
        self._sub.connect(cfg.status_endpoint)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._last_status: Optional[Dict[str, Any]] = None
        self._last_status_ts: Optional[int] = None
        self._acks: Dict[str, Dict[str, Any]] = {}
        self._exec_events: list[Dict[str, Any]] = []
        self._exec_max = 200
        self._last_warning_ts = 0.0

    def start(self) -> None:
        if self._thread:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        self._pub.close()
        self._sub.close()

    def send_command(self, cmd: str, payload: Dict[str, Any], ttl_ms: Optional[int] = None) -> str:
        command = build_command(
            bot_id=self._cfg.bot_id,
            symbol=self._cfg.symbol,
            cmd=cmd,
            payload=payload,
            ttl_ms=ttl_ms if ttl_ms is not None else self._cfg.cmd_ttl_ms,
        )
        data = msgspec.structs.asdict(command)
        ok, reason = validate_command(data)
        if not ok:
            raise ValueError(f"Invalid command: {reason}")
        self._pub.send_multipart([self._cfg.cmd_topic.encode("utf-8"), msgspec.msgpack.encode(command)])
        self._logger.info("Lockbot cmd sent cmd_id=%s cmd=%s", command.cmd_id, cmd)
        return command.cmd_id

    def status(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return dict(self._last_status) if self._last_status else None

    def ack(self, cmd_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._acks.get(cmd_id)

    def exec_recent(self, limit: int = 20) -> list[Dict[str, Any]]:
        with self._lock:
            if limit <= 0:
                return []
            return list(self._exec_events)[-limit:]

    def _reader_loop(self) -> None:
        poller = zmq.Poller()
        poller.register(self._sub, zmq.POLLIN)
        while not self._stop.is_set():
            try:
                socks = dict(poller.poll(200))
            except Exception:
                continue
            if self._sub in socks:
                topic, payload = self._sub.recv_multipart()
                text = topic.decode("utf-8", errors="ignore")
                if text == self._cfg.ack_topic:
                    try:
                        ack = msgspec.msgpack.decode(payload, type=AckEnvelope)
                        self._store_ack(msgspec.structs.asdict(ack))
                    except Exception:
                        continue
                elif text == self._cfg.status_topic:
                    try:
                        status = msgspec.msgpack.decode(payload, type=StatusEnvelope)
                        self._store_status(msgspec.structs.asdict(status))
                    except Exception:
                        continue
                elif text == self._cfg.exec_topic:
                    try:
                        event = msgspec.msgpack.decode(payload, type=ExecEnvelope)
                        self._store_exec(msgspec.structs.asdict(event))
                    except Exception:
                        continue
            self._check_stale()

    def _store_status(self, status: Dict[str, Any]) -> None:
        with self._lock:
            self._last_status = status
            self._last_status_ts = int(status.get("ts_event") or 0)

    def _store_ack(self, ack: Dict[str, Any]) -> None:
        cmd_id = str(ack.get("cmd_id") or "")
        if not cmd_id:
            return
        with self._lock:
            self._acks[cmd_id] = ack
        self._logger.info("Lockbot ack cmd_id=%s status=%s", cmd_id, ack.get("payload", {}).get("status"))

    def _store_exec(self, event: Dict[str, Any]) -> None:
        with self._lock:
            self._exec_events.append(event)
            while len(self._exec_events) > self._exec_max:
                self._exec_events.pop(0)

    def _check_stale(self) -> None:
        if not self._last_status_ts:
            return
        now_ms = int(time.time() * 1000)
        lag = now_ms - self._last_status_ts
        if lag > self._cfg.stale_after_ms and (time.time() - self._last_warning_ts) > 5:
            self._logger.warning("Lockbot status stale lag_ms=%s", lag)
            self._last_warning_ts = time.time()
