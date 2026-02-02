import os
import re
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

import yaml


class ScheduleValidationError(Exception):
    pass


DAY_NAMES = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}


def _normalize_days(days: List[str]) -> List[str]:
    if not days:
        return ["*"]
    normalized = []
    for day in days:
        d = str(day).strip().lower()
        if d in {"*", "all"}:
            return ["*"]
        normalized.append(d)
    return normalized or ["*"]


def _validate_days(days: List[str]) -> None:
    if "*" in days:
        return
    for day in days:
        if day not in DAY_NAMES:
            raise ScheduleValidationError(f"Invalid day '{day}' (expected mon..sun or '*').")


def _parse_hhmm(value: str) -> int:
    text = str(value).strip()
    if not re.match(r"^\d{2}:\d{2}$", text):
        raise ScheduleValidationError(f"Invalid time '{value}' (expected HH:MM).")
    hour = int(text[0:2])
    minute = int(text[3:5])
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ScheduleValidationError(f"Invalid time '{value}' (expected HH:MM).")
    return hour * 60 + minute


def _validate_cron_field(value: str, label: str, max_value: int) -> None:
    text = str(value).strip()
    if text == "*":
        return
    if text.startswith("*/"):
        step = text[2:]
        if not step.isdigit() or int(step) <= 0:
            raise ScheduleValidationError(f"Invalid cron {label} step '{value}'.")
        return
    if text.isdigit():
        num = int(text)
        if num < 0 or num > max_value:
            raise ScheduleValidationError(f"Invalid cron {label} value '{value}'.")
        return
    raise ScheduleValidationError(f"Unsupported cron {label} value '{value}'.")


@dataclass
class ScheduleWindow:
    days: List[str]
    start: str
    end: str

    def validate(self) -> None:
        days = _normalize_days(self.days)
        _validate_days(days)
        _parse_hhmm(self.start)
        _parse_hhmm(self.end)


@dataclass
class ScheduleTrigger:
    type: str
    every_seconds: Optional[int] = None
    minute: Optional[str] = None
    hour: Optional[str] = None

    def validate(self) -> None:
        if self.type == "interval":
            if self.every_seconds is None or int(self.every_seconds) <= 0:
                raise ScheduleValidationError("Trigger interval requires every_seconds > 0.")
        elif self.type == "cron":
            _validate_cron_field(self.minute or "*", "minute", 59)
            _validate_cron_field(self.hour or "*", "hour", 23)
        elif self.type == "once":
            return
        else:
            raise ScheduleValidationError(f"Unsupported trigger type '{self.type}'.")


@dataclass
class SchedulePolicy:
    max_concurrent: int = 1
    max_runs_per_window: int = 1
    max_attempts: int = 3


@dataclass
class ScheduleRetries:
    enabled: bool = True
    backoff_base_seconds: int = 15
    backoff_max_seconds: int = 300
    jitter: bool = True


@dataclass
class ScheduleSpec:
    schedule_id: str
    enabled: bool = True
    timezone: str = "Europe/Kyiv"
    project_id: str = ""
    inbox_dir: str = "runtime/inbox"
    archive_dir: str = "runtime/inbox_done"
    failed_dir: str = "runtime/inbox_failed"
    windows: List[ScheduleWindow] = field(default_factory=list)
    trigger: ScheduleTrigger = field(default_factory=lambda: ScheduleTrigger(type="interval", every_seconds=3600))
    task_template: Dict[str, Any] | str = field(default_factory=dict)
    policy: SchedulePolicy = field(default_factory=SchedulePolicy)
    retries: ScheduleRetries = field(default_factory=ScheduleRetries)

    def validate(self) -> None:
        errors = []
        if not self.schedule_id:
            errors.append("schedule_id is required")
        if not self.project_id:
            errors.append("project_id is required")
        if self.policy.max_concurrent <= 0:
            errors.append("policy.max_concurrent must be > 0")
        if self.policy.max_runs_per_window <= 0:
            errors.append("policy.max_runs_per_window must be > 0")
        if self.policy.max_attempts <= 0:
            errors.append("policy.max_attempts must be > 0")
        try:
            self.trigger.validate()
        except ScheduleValidationError as exc:
            errors.append(str(exc))
        for window in self.windows:
            try:
                window.validate()
            except ScheduleValidationError as exc:
                errors.append(str(exc))
        if errors:
            raise ScheduleValidationError("; ".join(errors))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _normalize_window(raw: dict) -> ScheduleWindow:
    return ScheduleWindow(
        days=_normalize_days(raw.get("days") or ["*"]),
        start=str(raw.get("start") or "00:00"),
        end=str(raw.get("end") or "23:59"),
    )


def _normalize_trigger(raw: dict) -> ScheduleTrigger:
    trigger_type = str(raw.get("type") or "interval")
    return ScheduleTrigger(
        type=trigger_type,
        every_seconds=raw.get("every_seconds"),
        minute=raw.get("minute"),
        hour=raw.get("hour"),
    )


def _normalize_policy(raw: dict) -> SchedulePolicy:
    return SchedulePolicy(
        max_concurrent=int(raw.get("max_concurrent", 1)),
        max_runs_per_window=int(raw.get("max_runs_per_window", 1)),
        max_attempts=int(raw.get("max_attempts", 3)),
    )


def _normalize_retries(raw: dict) -> ScheduleRetries:
    return ScheduleRetries(
        enabled=bool(raw.get("enabled", True)),
        backoff_base_seconds=int(raw.get("backoff_base_seconds", 15)),
        backoff_max_seconds=int(raw.get("backoff_max_seconds", 300)),
        jitter=bool(raw.get("jitter", True)),
    )


def _build_schedule_id(raw: dict, source: Optional[str]) -> str:
    schedule_id = raw.get("schedule_id")
    if schedule_id:
        return str(schedule_id)
    if source:
        base = os.path.splitext(os.path.basename(source))[0]
        if base:
            return base
    return f"schedule_{uuid.uuid4().hex[:6]}"


def _schedule_from_dict(raw: dict, source: Optional[str]) -> ScheduleSpec:
    if not isinstance(raw, dict):
        raise ScheduleValidationError("ScheduleSpec must be a mapping.")

    schedule_id = _build_schedule_id(raw, source)
    windows_raw = raw.get("windows") or []
    windows = [_normalize_window(item or {}) for item in windows_raw]
    trigger = _normalize_trigger(raw.get("trigger") or {})
    policy = _normalize_policy(raw.get("policy") or {})
    retries = _normalize_retries(raw.get("retries") or {})
    task_template = raw.get("task_template") or {}

    spec = ScheduleSpec(
        schedule_id=str(schedule_id),
        enabled=bool(raw.get("enabled", True)),
        timezone=str(raw.get("timezone") or "Europe/Kyiv"),
        project_id=str(raw.get("project_id") or ""),
        inbox_dir=str(raw.get("inbox_dir") or "runtime/inbox"),
        archive_dir=str(raw.get("archive_dir") or "runtime/inbox_done"),
        failed_dir=str(raw.get("failed_dir") or "runtime/inbox_failed"),
        windows=windows,
        trigger=trigger,
        task_template=task_template,
        policy=policy,
        retries=retries,
    )
    spec.validate()
    return spec


def load_schedule_file(path: str) -> List[ScheduleSpec]:
    if not os.path.exists(path):
        raise ScheduleValidationError(f"Schedule file not found: {path}")
    with open(path, "r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    if isinstance(raw, list):
        return [_schedule_from_dict(item or {}, path) for item in raw]
    if isinstance(raw, dict):
        if "schedules" in raw and isinstance(raw.get("schedules"), list):
            return [_schedule_from_dict(item or {}, path) for item in raw.get("schedules") or []]
        return [_schedule_from_dict(raw, path)]
    raise ScheduleValidationError("Schedule file must contain a mapping or list.")
