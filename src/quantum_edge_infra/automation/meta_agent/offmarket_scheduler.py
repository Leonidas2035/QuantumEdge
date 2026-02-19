from __future__ import annotations

import json
import os
import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import yaml

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - fallback
    ZoneInfo = None

from inbox_processor import process_inbox_once
from logger import configure_logger
from schedule_contract import (ScheduleSpec, ScheduleValidationError,
                               load_schedule_file)

try:
    from tools.qe_config import get_qe_paths
except Exception:  # pragma: no cover - fallback for legacy runs
    get_qe_paths = None


def _resolve_base_dir() -> str:
    env_root = os.getenv("QE_ROOT")
    if env_root:
        return env_root
    if get_qe_paths:
        try:
            return str(get_qe_paths()["qe_root"])
        except Exception:
            pass
    base = os.path.abspath(os.path.dirname(__file__))
    parent = os.path.abspath(os.path.join(base, os.pardir))
    if os.path.isdir(os.path.join(parent, "config")) and os.path.isdir(
        os.path.join(parent, "ai_scalper_bot")
    ):
        return parent
    return base


def _resolve_runtime_dir() -> str:
    base = _resolve_base_dir()
    base_abs = os.path.abspath(base)
    env_runtime = os.getenv("META_AGENT_RUNTIME_DIR") or os.getenv("QE_RUNTIME_DIR")
    if env_runtime:
        candidate = os.path.abspath(env_runtime)
        try:
            if os.path.commonpath([candidate, base_abs]) == base_abs:
                return candidate
        except ValueError:
            pass
    return os.path.abspath(os.path.join(base_abs, "runtime"))


def _resolve_schedules_dir(schedules_dir: Optional[str]) -> str:
    base_abs = os.path.abspath(_resolve_base_dir())
    if schedules_dir:
        candidate = schedules_dir
    else:
        candidate = os.path.join(_resolve_runtime_dir(), "schedules")
        if not os.path.isdir(candidate):
            candidate = os.path.join(base_abs, "schedules")
    if os.path.isabs(candidate):
        return candidate
    return os.path.abspath(os.path.join(base_abs, candidate))


def _state_path(runtime_dir: str) -> str:
    return os.path.join(runtime_dir, "scheduler", "state.json")


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def _format_iso(dt: Optional[datetime]) -> Optional[str]:
    if not dt:
        return None
    return dt.astimezone(timezone.utc).isoformat()


def _load_state(path: str) -> dict:
    if not os.path.exists(path):
        return {"last_tick": None, "schedules": {}}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle) or {}
    except Exception as exc:
        raise RuntimeError(f"Failed to read scheduler state: {exc}") from exc
    if "schedules" not in data:
        data["schedules"] = {}
    return data


def _save_state_atomic(path: str, state: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp_path = f"{path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2)
    os.replace(temp_path, path)


def load_schedules(schedules_dir: str) -> List[ScheduleSpec]:
    specs: List[ScheduleSpec] = []
    if not os.path.isdir(schedules_dir):
        return specs
    for entry in sorted(os.listdir(schedules_dir)):
        if not entry.lower().endswith((".yaml", ".yml")):
            continue
        path = os.path.join(schedules_dir, entry)
        specs.extend(load_schedule_file(path))
    return specs


def _tzinfo(tz_name: str):
    if ZoneInfo is None:
        raise ValueError("ZoneInfo unavailable; timezone support missing.")
    return ZoneInfo(tz_name)


def _minute_of_day(value: str) -> int:
    hour, minute = value.split(":")
    return int(hour) * 60 + int(minute)


def _day_matches(days: List[str], weekday: int) -> bool:
    if "*" in days:
        return True
    day_map = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
    return any(day_map.get(day) == weekday for day in days)


def evaluate_windows(now_local: datetime, windows) -> bool:
    if not windows:
        return True
    weekday = now_local.weekday()
    minutes = now_local.hour * 60 + now_local.minute
    for window in windows:
        days = window.days
        day_match = _day_matches(days, weekday)
        prev_day_match = _day_matches(days, (weekday - 1) % 7)
        start_min = _minute_of_day(window.start)
        end_min = _minute_of_day(window.end)
        if start_min <= end_min:
            if day_match and start_min <= minutes < end_min:
                return True
        else:
            if (day_match and minutes >= start_min) or (
                prev_day_match and minutes < end_min
            ):
                return True
    return False


def _window_end(now_local: datetime, window) -> datetime:
    start_min = _minute_of_day(window.start)
    end_min = _minute_of_day(window.end)
    if start_min <= end_min:
        end_time = now_local.replace(
            hour=end_min // 60, minute=end_min % 60, second=0, microsecond=0
        )
        if end_time < now_local:
            end_time += timedelta(days=1)
        return end_time
    end_time = now_local.replace(
        hour=end_min // 60, minute=end_min % 60, second=0, microsecond=0
    )
    if now_local.hour * 60 + now_local.minute < end_min:
        return end_time
    return end_time + timedelta(days=1)


def _cron_match(value: str, current: int) -> bool:
    text = str(value or "*").strip()
    if text == "*":
        return True
    if text.startswith("*/"):
        step = int(text[2:])
        return current % step == 0
    return current == int(text)


def compute_next_fire(
    schedule: ScheduleSpec, last_fire: Optional[datetime], now_local: datetime
) -> Optional[datetime]:
    trigger = schedule.trigger
    if trigger.type == "interval":
        if last_fire is None:
            return now_local
        return last_fire + timedelta(seconds=int(trigger.every_seconds or 0))
    if trigger.type == "once":
        return None if last_fire else now_local
    if trigger.type == "cron":
        for _ in range(0, 24 * 60):
            if _cron_match(trigger.minute or "*", now_local.minute) and _cron_match(
                trigger.hour or "*", now_local.hour
            ):
                return now_local
            now_local += timedelta(minutes=1)
        return None
    return None


def _trigger_due(
    schedule: ScheduleSpec, last_fire: Optional[datetime], now_local: datetime
) -> bool:
    trigger = schedule.trigger
    if trigger.type == "interval":
        if last_fire is None:
            return True
        return (now_local - last_fire).total_seconds() >= int(
            trigger.every_seconds or 0
        )
    if trigger.type == "once":
        return last_fire is None
    if trigger.type == "cron":
        if not _cron_match(trigger.minute or "*", now_local.minute):
            return False
        if not _cron_match(trigger.hour or "*", now_local.hour):
            return False
        if last_fire and last_fire.replace(
            second=0, microsecond=0
        ) == now_local.replace(second=0, microsecond=0):
            return False
        return True
    return False


def _schedule_task_name(schedule_id: str, now_local: datetime) -> str:
    stamp = now_local.strftime("%Y%m%d_%H%M%S")
    short = uuid.uuid4().hex[:6]
    return f"{schedule_id}__{stamp}__{short}.task.yaml"


def _resolve_path_under_base(path: str, base_abs: str) -> str:
    candidate = path if os.path.isabs(path) else os.path.join(base_abs, path)
    candidate = os.path.abspath(candidate)
    if os.path.commonpath([candidate, base_abs]) != base_abs:
        raise ValueError("Path escapes repo root")
    return candidate


def enqueue_task(
    schedule: ScheduleSpec, task_template: dict, inbox_dir: str, now_local: datetime
) -> str:
    os.makedirs(inbox_dir, exist_ok=True)
    payload = dict(task_template)
    payload.setdefault("project_id", schedule.project_id)
    payload.setdefault(
        "task_id", f"{schedule.schedule_id}_{now_local.strftime('%Y%m%d_%H%M%S')}"
    )
    payload.setdefault("created_at", now_local.astimezone(timezone.utc).isoformat())
    payload.setdefault("metadata", {})
    payload["metadata"]["schedule_id"] = schedule.schedule_id

    task_name = _schedule_task_name(schedule.schedule_id, now_local)
    final_path = os.path.join(inbox_dir, task_name)
    temp_path = f"{final_path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, allow_unicode=True, sort_keys=False)
    os.replace(temp_path, final_path)
    return task_name


def _load_task_template(schedule: ScheduleSpec, base_abs: str) -> dict:
    if isinstance(schedule.task_template, dict):
        return schedule.task_template
    if isinstance(schedule.task_template, str):
        template_path = schedule.task_template
        resolved = _resolve_path_under_base(template_path, base_abs)
        with open(resolved, "r", encoding="utf-8") as handle:
            return yaml.safe_load(handle) or {}
    return {}


def _task_status_locations(schedule: ScheduleSpec, base_abs: str) -> dict:
    return {
        "inbox": _resolve_path_under_base(schedule.inbox_dir, base_abs),
        "archive": _resolve_path_under_base(schedule.archive_dir, base_abs),
        "failed": _resolve_path_under_base(schedule.failed_dir, base_abs),
    }


def _find_task_file(task_name: str, locations: dict) -> Optional[str]:
    for path in locations.values():
        candidate = os.path.join(path, task_name)
        if os.path.exists(candidate):
            return candidate
    return None


def _find_task_by_suffix(original_name: str, folder: str) -> Optional[str]:
    if not os.path.isdir(folder):
        return None
    for entry in os.listdir(folder):
        if entry.endswith(f"_{original_name}"):
            return os.path.join(folder, entry)
    return None


def _extract_run_id(archived_name: str, original_name: str) -> Optional[str]:
    suffix = f"_{original_name}"
    if archived_name.endswith(suffix):
        return archived_name[: -len(suffix)]
    return None


def _extract_schedule_id(task_name: str) -> Optional[str]:
    if "__" not in task_name:
        return None
    return task_name.split("__", 1)[0]


def _calc_backoff(attempts: int, base: int, cap: int, jitter: bool) -> int:
    delay = min(base * (2 ** max(attempts - 1, 0)), cap)
    if jitter:
        spread = int(delay * 0.2)
        if spread > 0:
            delay += random.randint(-spread, spread)
    return max(0, delay)


def _is_transient(exit_code: int) -> bool:
    return exit_code in {30, 50}


def _update_window_runs(state_entry: dict, now_local: datetime) -> None:
    day_key = now_local.strftime("%Y%m%d")
    window_runs = state_entry.setdefault("window_runs", {})
    window_runs[day_key] = int(window_runs.get(day_key, 0)) + 1


def _should_skip_due_to_pending(task_name: Optional[str], locations: dict) -> bool:
    if not task_name:
        return False
    return _find_task_file(os.path.basename(task_name), locations) is not None


def tick(
    schedules_dir: str,
    runtime_dir: str,
    logger,
    now_utc: Optional[datetime] = None,
) -> dict:
    base_abs = os.path.abspath(_resolve_base_dir())
    schedules = load_schedules(schedules_dir)
    state_path = _state_path(runtime_dir)
    state = _load_state(state_path)
    state["last_tick"] = _format_iso(now_utc or datetime.now(timezone.utc))

    if not schedules:
        _save_state_atomic(state_path, state)
        return {"processed": 0, "enqueued": 0, "status": "no_schedules"}

    stop_detected = False
    pause_detected = False
    for schedule in schedules:
        locations = _task_status_locations(schedule, base_abs)
        if os.path.exists(os.path.join(locations["inbox"], "STOP")):
            stop_detected = True
        if os.path.exists(os.path.join(locations["inbox"], "PAUSE")):
            pause_detected = True
    if stop_detected:
        _save_state_atomic(state_path, state)
        return {"processed": 0, "enqueued": 0, "stop": True}
    if pause_detected:
        _save_state_atomic(state_path, state)
        return {"processed": 0, "enqueued": 0, "pause": True}

    enqueued = 0
    for schedule in schedules:
        entry = state["schedules"].setdefault(schedule.schedule_id, {})
        if not schedule.enabled:
            continue

        tz = _tzinfo(schedule.timezone)
        now_local = (now_utc or datetime.now(timezone.utc)).astimezone(tz)
        locations = _task_status_locations(schedule, base_abs)

        pending_name = entry.get("last_task_file")
        if pending_name:
            pending_base = os.path.basename(pending_name)
            inbox_hit = os.path.join(locations["inbox"], pending_base)
            archive_hit = _find_task_by_suffix(pending_base, locations["archive"])
            failed_hit = _find_task_by_suffix(pending_base, locations["failed"])
            if os.path.exists(inbox_hit):
                continue
            if archive_hit or failed_hit:
                entry["last_task_file"] = pending_base
                archived_name = os.path.basename(archive_hit or failed_hit)
                run_id = _extract_run_id(archived_name, pending_base)
                if run_id:
                    entry["last_run_id"] = run_id
                    report_path = os.path.join(
                        runtime_dir, "runs", run_id, "report.json"
                    )
                    try:
                        with open(report_path, "r", encoding="utf-8") as handle:
                            report_data = json.load(handle)
                        entry["last_exit_code"] = report_data.get("exit_code")
                    except Exception:
                        entry.setdefault("last_exit_code", None)
            else:
                entry["last_task_missing"] = True

        last_fire = _parse_iso(entry.get("last_fire"))
        next_eligible_at = _parse_iso(entry.get("next_eligible_at"))
        if (
            next_eligible_at
            and (now_utc or datetime.now(timezone.utc)) < next_eligible_at
        ):
            continue

        if not evaluate_windows(now_local, schedule.windows):
            continue

        window_runs = entry.get("window_runs", {})
        day_key = now_local.strftime("%Y%m%d")
        if int(window_runs.get(day_key, 0)) >= schedule.policy.max_runs_per_window:
            continue

        pending_name = entry.get("last_task_file")
        if _should_skip_due_to_pending(pending_name, locations):
            continue

        if not _trigger_due(schedule, last_fire, now_local):
            continue

        task_template = _load_task_template(schedule, base_abs)
        task_name = enqueue_task(schedule, task_template, locations["inbox"], now_local)
        entry["last_fire"] = _format_iso(now_local)
        entry["last_task_file"] = task_name
        entry["attempts"] = 0
        entry["next_eligible_at"] = None
        enqueued += 1

    processed_total = 0
    reports_total = []
    for schedule in schedules:
        locations = _task_status_locations(schedule, base_abs)
        result = process_inbox_once(
            inbox=locations["inbox"],
            archive=locations["archive"],
            failed=locations["failed"],
            logger=logger,
        )
        processed_total += result.get("processed", 0)
        reports_total.extend(result.get("reports", []))
        if result.get("lock_busy"):
            for entry in state["schedules"].values():
                backoff = _calc_backoff(1, 5, 30, False)
                entry["next_eligible_at"] = _format_iso(
                    (now_utc or datetime.now(timezone.utc)) + timedelta(seconds=backoff)
                )

    for item in reports_total:
        task_name = item.get("task_name")
        schedule_id = _extract_schedule_id(task_name or "")
        if not schedule_id or schedule_id not in state["schedules"]:
            continue
        entry = state["schedules"][schedule_id]
        report = item.get("report")
        exit_code = int(getattr(report, "exit_code", 30))
        entry["last_run_id"] = getattr(report, "run_id", None)
        entry["last_exit_code"] = exit_code
        entry["last_task_file"] = item.get("task_name")

        schedule = next((s for s in schedules if s.schedule_id == schedule_id), None)
        if schedule is None:
            continue
        tz = _tzinfo(schedule.timezone)
        now_local = (now_utc or datetime.now(timezone.utc)).astimezone(tz)
        _update_window_runs(entry, now_local)

        if schedule.retries.enabled and _is_transient(exit_code):
            attempts = int(entry.get("attempts", 0)) + 1
            entry["attempts"] = attempts
            delay = _calc_backoff(
                attempts,
                schedule.retries.backoff_base_seconds,
                schedule.retries.backoff_max_seconds,
                schedule.retries.jitter,
            )
            entry["next_eligible_at"] = _format_iso(
                (now_utc or datetime.now(timezone.utc)) + timedelta(seconds=delay)
            )
            if attempts >= schedule.policy.max_attempts:
                entry["attempts"] = 0
                end_time = (
                    _window_end(now_local, schedule.windows[0])
                    if schedule.windows
                    else now_local + timedelta(hours=1)
                )
                entry["next_eligible_at"] = _format_iso(
                    end_time.astimezone(timezone.utc)
                )
        else:
            entry["attempts"] = 0
            entry["next_eligible_at"] = None

    _save_state_atomic(state_path, state)
    return {
        "processed": processed_total,
        "enqueued": enqueued,
        "state_path": state_path,
    }


def status(schedules_dir: str, runtime_dir: str) -> List[dict]:
    base_abs = os.path.abspath(_resolve_base_dir())
    schedules = load_schedules(schedules_dir)
    state = _load_state(_state_path(runtime_dir))

    payload = []
    for schedule in schedules:
        entry = state.get("schedules", {}).get(schedule.schedule_id, {})
        tz = _tzinfo(schedule.timezone)
        now_local = datetime.now(timezone.utc).astimezone(tz)
        in_window = evaluate_windows(now_local, schedule.windows)
        payload.append(
            {
                "schedule_id": schedule.schedule_id,
                "enabled": schedule.enabled,
                "in_window": in_window,
                "next_eligible_at": entry.get("next_eligible_at"),
                "last_exit_code": entry.get("last_exit_code"),
                "attempts": entry.get("attempts", 0),
            }
        )
    return payload


def main(
    schedules_dir: Optional[str] = None,
    tick_seconds: int = 2,
    once: bool = False,
    json_mode: bool = False,
    quiet: bool = False,
) -> int:
    runtime_dir = _resolve_runtime_dir()
    log_level = (os.getenv("META_AGENT_LOG_LEVEL") or "INFO").upper()
    logger = configure_logger("meta_agent.scheduler", runtime_dir, log_level)
    schedules_root = _resolve_schedules_dir(schedules_dir)

    try:
        while True:
            result = tick(schedules_root, runtime_dir, logger)
            if result.get("stop"):
                if not quiet:
                    print("[INFO] STOP detected; exiting.")
                return 0
            if once:
                if json_mode:
                    print(json.dumps(result, ensure_ascii=True))
                return 0
            if result.get("pause"):
                if not quiet:
                    print("[INFO] PAUSE detected; scheduler idle.")
            if tick_seconds > 0:
                import time

                time.sleep(tick_seconds)
    except ScheduleValidationError as exc:
        if not quiet:
            print(f"[ERROR] {exc}")
        return 1
    except Exception as exc:
        if not quiet:
            print(f"[ERROR] Scheduler failed: {exc}")
        return 2
