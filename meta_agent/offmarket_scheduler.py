from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from offmarket_state import OffmarketState, load_offmarket_state, save_offmarket_state
from projects_config import load_project_registry
from supervisor_runner import run_supervisor_maintenance_once

SCHEDULE_CFG_PATH = Path("config/offmarket_schedule.yaml")
STATE_PATH = Path("state/offmarket_state.json")
LOG_PATH = Path("logs/offmarket_scheduler.log")


def _load_schedule() -> dict:
    import yaml

    if not SCHEDULE_CFG_PATH.exists():
        return {"enabled": False}
    with SCHEDULE_CFG_PATH.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _setup_logging() -> logging.Logger:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("offmarket_scheduler")
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        fh = logging.FileHandler(LOG_PATH, encoding="utf-8")
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        logger.addHandler(sh)
    return logger


def _bot_idle(schedule_cfg: dict, logger: logging.Logger) -> bool:
    if not schedule_cfg.get("require_bot_idle", True):
        return True
    status_file = schedule_cfg.get("bot_status_file")
    if not status_file:
        logger.warning("require_bot_idle enabled but bot_status_file not set; treating as busy.")
        return False
    path = Path(status_file)
    if not path.exists():
        logger.warning("bot_status_file missing: %s", path)
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        is_trading = bool(data.get("is_trading", False))
        open_positions = int(data.get("open_positions", 0))
        return (not is_trading) and open_positions == 0
    except Exception as exc:
        logger.warning("Failed to read bot status: %s", exc)
        return False


def _within_window(schedule_cfg: dict, now: datetime) -> bool:
    window = schedule_cfg.get("window", {}) or {}
    start_h = int(window.get("start_hour_utc", 0))
    end_h = int(window.get("end_hour_utc", 24))
    hour = now.hour
    if start_h <= end_h:
        return start_h <= hour < end_h
    # wrap-around window
    return hour >= start_h or hour < end_h


def _day_allowed(schedule_cfg: dict, now: datetime) -> bool:
    days = schedule_cfg.get("days", {}) or {}
    weekday = now.weekday()  # 0=Mon
    if weekday < 5:
        return bool(days.get("allow_weekdays", True))
    return bool(days.get("allow_weekends", True))


def main() -> None:
    schedule_cfg = _load_schedule()
    logger = _setup_logging()

    if not schedule_cfg.get("enabled", False):
        logger.info("Offmarket scheduler disabled; exiting.")
        return

    now = datetime.now(timezone.utc)
    state = load_offmarket_state(STATE_PATH)

    if not _within_window(schedule_cfg, now):
        logger.info("Outside allowed window; skipping.")
        return
    if not _day_allowed(schedule_cfg, now):
        logger.info("Day not allowed; skipping.")
        return

    # reset runs_today if new day
    if state.last_run_utc and state.last_run_utc.date() != now.date():
        state.runs_today = 0

    max_runs = int(schedule_cfg.get("max_runs_per_day", 1))
    if state.runs_today >= max_runs:
        logger.info("Run limit reached for today (%s >= %s)", state.runs_today, max_runs)
        return

    if not _bot_idle(schedule_cfg, logger):
        logger.info("Bot not idle; skipping.")
        return

    registry = load_project_registry()
    try:
        result = run_supervisor_maintenance_once(registry, schedule_cfg)
        state.last_run_utc = now
        state.runs_today += 1
        state.last_run_result = result.get("status")
        save_offmarket_state(STATE_PATH, state)
        logger.info("Offmarket maintenance completed with status=%s", result.get("status"))
    except Exception as exc:
        state.last_run_utc = now
        state.last_run_result = f"error: {exc}"
        save_offmarket_state(STATE_PATH, state)
        logger.error("Offmarket maintenance failed: %s", exc)


if __name__ == "__main__":
    main()
