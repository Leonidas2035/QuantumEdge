"""Structured event logging for SupervisorAgent."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from supervisor.process_manager import ProcessInfo
    from supervisor.risk_engine import OrderRequest, RiskDecision

from supervisor.snapshot_models import SnapshotReport
from supervisor.tsdb.writer import TsdbWriter

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


@dataclass
class BaseEvent:
    """Core structured event."""

    ts: datetime
    type: EventType
    source: str
    data: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ts": self.ts.isoformat(),
            "type": self.type.value,
            "source": self.source,
            "data": self.data,
        }


class EventLogger:
    """Append-only JSONL event logger."""

    def __init__(self, events_path: Path, logger: Optional[logging.Logger] = None, snapshots_dir: Optional[Path] = None, tsdb_writer: Optional[TsdbWriter] = None) -> None:
        self.events_path = events_path
        self.logger = logger or logging.getLogger(__name__)
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        self.snapshots_dir = snapshots_dir
        if self.snapshots_dir:
            self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.tsdb_writer = tsdb_writer

    def log_event(self, event: BaseEvent) -> None:
        try:
            with self.events_path.open("a", encoding="utf-8") as handle:
                json.dump(event.to_dict(), handle)
                handle.write("\n")
        except Exception as exc:
            self.logger.warning("Failed to write event %s: %s", event.type, exc)
        if self.tsdb_writer:
            try:
                # Lazy import to avoid circular dependency during module import time
                from supervisor.tsdb import mappers as tsdb_mappers  # type: ignore

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
            data={"mode": mode, "pid": process_info.pid, "start_time": process_info.start_time.isoformat() if process_info.start_time else None},
        )
        self.log_event(event)

    def log_bot_stop(self, reason: str, process_info: Optional["ProcessInfo"]) -> None:
        data: Dict[str, Any] = {"reason": reason}
        if process_info:
            data.update(
                {
                    "pid": process_info.pid,
                    "start_time": process_info.start_time.isoformat() if process_info.start_time else None,
                    "exit_code": process_info.last_exit_code,
                    "exit_time": process_info.last_exit_time.isoformat() if process_info.last_exit_time else None,
                }
            )
        event = BaseEvent(ts=self._now(), type=EventType.BOT_STOP, source="ProcessManager", data=data)
        self.log_event(event)

    def log_order_decision(self, order: "OrderRequest", decision: "RiskDecision") -> None:
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
        event = BaseEvent(ts=self._now(), type=EventType.ORDER_DECISION, source="RiskEngine", data=data)
        self.log_event(event)

    def log_risk_limit_breach(self, code: str, details: Mapping[str, Any]) -> None:
        data = {"code": code, **details}
        event = BaseEvent(ts=self._now(), type=EventType.RISK_LIMIT_BREACH, source="RiskEngine", data=data)
        self.log_event(event)

    def log_mode_change(self, old_mode: str, new_mode: str, reason: str) -> None:
        event = BaseEvent(
            ts=self._now(),
            type=EventType.MODE_CHANGE,
            source="Supervisor",
            data={"old_mode": old_mode, "new_mode": new_mode, "reason": reason},
        )
        self.log_event(event)

    def log_anomaly(self, kind: str, message: str, extra: Optional[Mapping[str, Any]] = None) -> None:
        data: Dict[str, Any] = {"kind": kind, "message": message}
        if extra:
            data.update(extra)
        event = BaseEvent(ts=self._now(), type=EventType.ANOMALY, source="Supervisor", data=data)
        self.log_event(event)

    def log_order_result(self, result: str, data: Mapping[str, Any]) -> None:
        payload = {"result": result, **data}
        event = BaseEvent(ts=self._now(), type=EventType.ORDER_RESULT, source="RiskEngine", data=payload)
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
        event = BaseEvent(ts=self._now(), type=EventType.LLM_ADVICE, source="LlmSupervisor", data=data)
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

    def log_meta_supervisor_result(self, status: str, reports: Mapping[str, Any] | list) -> None:
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
            filename = self.snapshots_dir / f"snapshots_{snapshot.timestamp.date().isoformat()}.jsonl"
            try:
                with filename.open("a", encoding="utf-8") as handle:
                    json.dump(data, handle)
                    handle.write("\n")
            except Exception as exc:
                self.logger.warning("Failed to write snapshot log: %s", exc)
