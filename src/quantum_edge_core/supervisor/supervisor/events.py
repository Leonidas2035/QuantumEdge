"""Structured event logging for SupervisorAgent."""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from quantum_edge_core.supervisor.supervisor.process_manager import ProcessInfo
    from quantum_edge_core.supervisor.supervisor.risk_engine import (
        OrderRequest,
        RiskDecision,
    )

from quantum_edge_core.supervisor.supervisor.snapshot_models import SnapshotReport
from quantum_edge_core.supervisor.supervisor.tsdb.writer import TsdbWriter

SCHEMA_VERSION = "telemetry.v1"


class EventType(str, Enum):
    ORDER_DECISION = "ORDER_DECISION"
    ORDER_RESULT = "ORDER_RESULT"
    BOT_START = "BOT_START"
    BOT_STOP = "BOT_STOP"
    MODE_CHANGE = "MODE_CHANGE"
    RISK_LIMIT_BREACH = "RISK_LIMIT_BREACH"
    ANOMALY = "ANOMALY"
    LLM_ADVICE = "LLM_ADVICE"
    META_SUPERVISOR_RUN = "META_SUPERVISOR_RUN"
    META_SUPERVISOR_RESULT = "META_SUPERVISOR_RESULT"
    META_SUPERVISOR_SKIPPED = "META_SUPERVISOR_SKIPPED"
    SUPERVISOR_SNAPSHOT = "SUPERVISOR_SNAPSHOT"
    STRATEGY_UPDATE = "STRATEGY_UPDATE"
    PROCESS_START = "PROCESS_START"
    PROCESS_STOP = "PROCESS_STOP"
    PROCESS_EXIT = "PROCESS_EXIT"
    PROCESS_RESTART = "PROCESS_RESTART"
    HEALTH_OK = "HEALTH_OK"
    HEALTH_FAIL = "HEALTH_FAIL"
    API_CALL = "API_CALL"


@dataclass
class BaseEvent:
    """Core structured event."""

    ts: datetime
    type: EventType
    source: str
    data: Dict[str, Any]
    severity: str = "INFO"
    run_id: str = ""
    trace_id: Optional[str] = None
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> Dict[str, Any]:
        ts_ms = int(self.ts.replace(tzinfo=timezone.utc).timestamp() * 1000)
        component = self.source
        fields = dict(self.data)
        if component not in {"supervisor", "hub"} and not component.startswith("bot:"):
            fields.setdefault("component_detail", component)
            component = "supervisor"
        return {
            "ts_ms": ts_ms,
            "schema_version": self.schema_version,
            "component": component,
            "event_type": self.type.value,
            "severity": self.severity,
            "run_id": self.run_id,
            "trace_id": self.trace_id,
            "fields": fields,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Optional["BaseEvent"]:
        if not isinstance(raw, dict):
            return None
        if "schema_version" in raw or "event_type" in raw:
            return _parse_v1_event(raw)
        return _parse_legacy_event(raw)


class EventLogger:
    """Append-only JSONL event logger."""

    def __init__(
        self,
        events_path: Path,
        logger: Optional[logging.Logger] = None,
        snapshots_dir: Optional[Path] = None,
        tsdb_writer: Optional[TsdbWriter] = None,
        run_id: str = "",
    ) -> None:
        self.events_path = events_path
        self.logger = logger or logging.getLogger(__name__)
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        self.snapshots_dir = snapshots_dir
        if self.snapshots_dir:
            self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.tsdb_writer = tsdb_writer
        self.run_id = run_id

    def log_event(self, event: BaseEvent) -> None:
        if not event.run_id:
            event.run_id = self.run_id
        try:
            with self.events_path.open("a", encoding="utf-8") as handle:
                json.dump(event.to_dict(), handle)
                handle.write("\n")
        except Exception as exc:
            self.logger.warning("Failed to write event %s: %s", event.type, exc)
        if self.tsdb_writer:
            try:
                # Lazy import to avoid circular dependency during module import time
                from quantum_edge_core.supervisor.supervisor.tsdb import mappers as tsdb_mappers  # type: ignore

                points = tsdb_mappers.event_to_points(event)
                if points:
                    self.tsdb_writer.enqueue(points)
            except Exception as exc:  # pylint: disable=broad-except
                self.logger.debug("TSDB enqueue skipped: %s", exc)

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def log_bot_start(self, mode: str, process_info: "ProcessInfo") -> None:
        event = BaseEvent(
            ts=self._now(),
            type=EventType.BOT_START,
            source="ProcessManager",
            data={
                "mode": mode,
                "pid": process_info.pid,
                "start_time": (
                    process_info.start_time.isoformat()
                    if process_info.start_time
                    else None
                ),
            },
            severity="INFO",
        )
        self.log_event(event)

    def log_bot_stop(self, reason: str, process_info: Optional["ProcessInfo"]) -> None:
        data: Dict[str, Any] = {"reason": reason}
        if process_info:
            data.update(
                {
                    "pid": process_info.pid,
                    "start_time": (
                        process_info.start_time.isoformat()
                        if process_info.start_time
                        else None
                    ),
                    "exit_code": process_info.last_exit_code,
                    "exit_time": (
                        process_info.last_exit_time.isoformat()
                        if process_info.last_exit_time
                        else None
                    ),
                }
            )
        event = BaseEvent(
            ts=self._now(), type=EventType.BOT_STOP, source="ProcessManager", data=data
        )
        self.log_event(event)

    def log_order_decision(
        self, order: "OrderRequest", decision: "RiskDecision"
    ) -> None:
        data = {
            "symbol": order.symbol,
            "side": order.side.value,
            "order_type": order.order_type.value,
            "quantity": order.quantity,
            "price": order.price,
            "notional": order.notional,
            "leverage": order.leverage,
            "is_reduce_only": order.is_reduce_only,
            "allowed": decision.allowed,
            "code": decision.code,
            "reason": decision.reason,
        }
        event = BaseEvent(
            ts=self._now(),
            type=EventType.ORDER_DECISION,
            source="RiskEngine",
            data=data,
        )
        self.log_event(event)

    def log_risk_limit_breach(self, code: str, details: Mapping[str, Any]) -> None:
        data = {"code": code, **details}
        event = BaseEvent(
            ts=self._now(),
            type=EventType.RISK_LIMIT_BREACH,
            source="RiskEngine",
            data=data,
        )
        self.log_event(event)

    def log_mode_change(self, old_mode: str, new_mode: str, reason: str) -> None:
        event = BaseEvent(
            ts=self._now(),
            type=EventType.MODE_CHANGE,
            source="Supervisor",
            data={"old_mode": old_mode, "new_mode": new_mode, "reason": reason},
        )
        self.log_event(event)

    def log_anomaly(
        self, kind: str, message: str, extra: Optional[Mapping[str, Any]] = None
    ) -> None:
        data: Dict[str, Any] = {"kind": kind, "message": message}
        if extra:
            data.update(extra)
        event = BaseEvent(
            ts=self._now(), type=EventType.ANOMALY, source="Supervisor", data=data
        )
        self.log_event(event)

    def log_order_result(self, result: str, data: Mapping[str, Any]) -> None:
        payload = {"result": result, **data}
        event = BaseEvent(
            ts=self._now(),
            type=EventType.ORDER_RESULT,
            source="RiskEngine",
            data=payload,
        )
        self.log_event(event)

    def log_llm_advice(
        self,
        action: str,
        risk_multiplier: Optional[float],
        reason: str,
        dry_run: bool,
        extra: Optional[Mapping[str, Any]] = None,
    ) -> None:
        data: Dict[str, Any] = {
            "action": action,
            "risk_multiplier": risk_multiplier,
            "reason": reason,
            "dry_run": dry_run,
        }
        if extra:
            data.update(extra)
        event = BaseEvent(
            ts=self._now(), type=EventType.LLM_ADVICE, source="LlmSupervisor", data=data
        )
        self.log_event(event)

    def log_meta_supervisor_run_started(self, reason: str) -> None:
        event = BaseEvent(
            ts=self._now(),
            type=EventType.META_SUPERVISOR_RUN,
            source="MetaSupervisor",
            data={"reason": reason},
        )
        self.log_event(event)

    def log_meta_supervisor_run_skipped(self, reason: str) -> None:
        event = BaseEvent(
            ts=self._now(),
            type=EventType.META_SUPERVISOR_SKIPPED,
            source="MetaSupervisor",
            data={"reason": reason},
        )
        self.log_event(event)

    def log_meta_supervisor_result(
        self, status: str, reports: Mapping[str, Any] | list
    ) -> None:
        rep_list = reports if isinstance(reports, list) else []
        event = BaseEvent(
            ts=self._now(),
            type=EventType.META_SUPERVISOR_RESULT,
            source="MetaSupervisor",
            data={"status": status, "reports": [str(p) for p in rep_list]},
        )
        self.log_event(event)

    def log_supervisor_snapshot(self, snapshot: SnapshotReport) -> None:
        """Log a Supervisor snapshot both to the main event log and dedicated snapshot file."""

        data = snapshot.to_dict()
        event = BaseEvent(
            ts=snapshot.timestamp,
            type=EventType.SUPERVISOR_SNAPSHOT,
            source="Supervisor",
            data=data,
        )
        self.log_event(event)

        if self.snapshots_dir:
            filename = (
                self.snapshots_dir
                / f"snapshots_{snapshot.timestamp.date().isoformat()}.jsonl"
            )
            try:
                with filename.open("a", encoding="utf-8") as handle:
                    json.dump(data, handle)
                    handle.write("\n")
            except Exception as exc:
                self.logger.warning("Failed to write snapshot log: %s", exc)


def new_run_id() -> str:
    return uuid.uuid4().hex


def new_trace_id() -> str:
    return uuid.uuid4().hex


def tail_events(
    events_path: Path,
    limit: int = 200,
    types: Optional[Iterable[str]] = None,
    since_ts_ms: Optional[int] = None,
) -> list[dict]:
    if limit <= 0:
        return []
    if not events_path.exists():
        return []
    type_set = {t.upper() for t in types or []}
    lines = _read_last_lines(events_path, max_lines=limit * 5)
    results: list[dict] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        event = BaseEvent.from_dict(raw)
        if not event:
            continue
        if type_set and event.type.value.upper() not in type_set:
            continue
        ts_ms = int(event.ts.replace(tzinfo=timezone.utc).timestamp() * 1000)
        if since_ts_ms and ts_ms < since_ts_ms:
            continue
        results.append(event.to_dict())
        if len(results) >= limit:
            break
    return results


def prune_event_logs(
    events_dir: Path, retention_days: int, logger: Optional[logging.Logger] = None
) -> int:
    log = logger or logging.getLogger(__name__)
    if retention_days <= 0:
        return 0
    if not events_dir.exists():
        return 0
    cutoff = datetime.now(timezone.utc).date().toordinal() - retention_days
    deleted = 0
    for path in events_dir.glob("events_*.jsonl"):
        date_part = path.stem.replace("events_", "")
        try:
            file_date = datetime.fromisoformat(date_part).date()
        except ValueError:
            continue
        if file_date.toordinal() <= cutoff:
            try:
                path.unlink()
                deleted += 1
            except OSError as exc:
                log.debug("Failed to delete %s: %s", path, exc)
    return deleted


def _read_last_lines(
    path: Path,
    max_lines: int = 200,
    chunk_size: int = 8192,
    max_bytes: int = 1024 * 1024,
) -> list[str]:
    lines: list[str] = []
    size = 0
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)  # type: ignore[attr-defined]
        position = handle.tell()
        buffer = b""
        while position > 0 and len(lines) <= max_lines and size < max_bytes:
            read_size = min(chunk_size, position)
            position -= read_size
            handle.seek(position)
            data = handle.read(read_size)
            size += len(data)
            buffer = data + buffer
            while b"\n" in buffer:
                buffer, line = buffer.rsplit(b"\n", 1)
                if not line:
                    continue
                lines.append(line.decode("utf-8", errors="ignore"))
                if len(lines) >= max_lines:
                    break
            if len(lines) >= max_lines:
                break
    return lines


def _parse_v1_event(raw: Mapping[str, Any]) -> Optional[BaseEvent]:
    event_type = str(raw.get("event_type") or raw.get("type") or "ANOMALY")
    component = str(raw.get("component") or raw.get("source") or "unknown")
    fields = (
        raw.get("fields")
        if isinstance(raw.get("fields"), dict)
        else raw.get("data") or {}
    )
    severity = str(raw.get("severity") or "INFO")
    run_id = str(raw.get("run_id") or "")
    trace_id = raw.get("trace_id")
    ts_ms = raw.get("ts_ms")
    ts = _ts_from_any(ts_ms) or _ts_from_any(raw.get("ts"))
    if not ts:
        ts = datetime.now(timezone.utc)
    event = BaseEvent(
        ts=ts,
        type=_coerce_event_type(event_type),
        source=component,
        data=dict(fields),
        severity=severity,
        run_id=run_id,
        trace_id=str(trace_id) if trace_id else None,
    )
    if event.type == EventType.ANOMALY and event_type != EventType.ANOMALY.value:
        event.data.setdefault("original_event_type", event_type)
    return event


def _parse_legacy_event(raw: Mapping[str, Any]) -> Optional[BaseEvent]:
    try:
        ts = datetime.fromisoformat(str(raw.get("ts")))
    except Exception:
        ts = datetime.now(timezone.utc)
    event_type = str(raw.get("type") or "ANOMALY")
    source = str(raw.get("source") or "unknown")
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    return BaseEvent(
        ts=ts,
        type=_coerce_event_type(event_type),
        source=source,
        data=dict(data),
    )


def _coerce_event_type(value: str) -> EventType:
    try:
        return EventType(value)
    except Exception:
        return EventType.ANOMALY


def _ts_from_any(value: object) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc)
        except Exception:
            return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
