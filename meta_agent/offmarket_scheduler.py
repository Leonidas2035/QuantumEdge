from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from offmarket_state import OffmarketState, load_offmarket_state, save_offmarket_state
from projects_config import load_project_registry
from supervisor_runner import run_supervisor_maintenance_once

try:
    from tools.qe_config import get_qe_paths
except Exception:  # pragma: no cover - fallback for legacy runs
    get_qe_paths = None


def _resolve_base_dir() -> Path:
    env_root = os.getenv("QE_ROOT")
    if env_root:
        return Path(env_root)
    if get_qe_paths:
        try:
            return get_qe_paths()["qe_root"]
        except Exception:
            pass
    base = Path(__file__).resolve().parent
    parent = base.parent
    if (parent / "config").is_dir() and (parent / "ai_scalper_bot").is_dir():
        return parent
    return base


def _resolve_schedule_path() -> Path:
    base = _resolve_base_dir()
    override = os.getenv("META_AGENT_OFFMARKET_CONFIG")
    if override:
        candidate = Path(override)
    else:
        meta_cfg = base / "config" / "meta_agent.yaml"
        if meta_cfg.exists():
            try:
                import yaml

                raw = yaml.safe_load(meta_cfg.read_text(encoding="utf-8")) or {}
                candidate = Path(raw.get("offmarket_schedule_path", "config/offmarket_schedule.yaml"))
            except yaml.YAMLError:
                candidate = Path("config/offmarket_schedule.yaml")
        else:
            candidate = Path("config/offmarket_schedule.yaml")
    if not candidate.is_absolute():
        candidate = base / candidate
    return candidate.resolve()


def _resolve_state_path() -> Path:
    base = _resolve_base_dir()
    runtime_dir = os.getenv("QE_RUNTIME_DIR")
    if runtime_dir:
        return Path(runtime_dir).resolve() / "meta_agent" / "offmarket_state.json"
    return base / "runtime" / "meta_agent" / "offmarket_state.json"


def _resolve_log_path() -> Path:
    base = _resolve_base_dir()
    logs_dir = os.getenv("QE_LOGS_DIR")
    if logs_dir:
        return Path(logs_dir).resolve() / "offmarket_scheduler.log"
    return base / "logs" / "offmarket_scheduler.log"


def _load_schedule() -> dict:
    import yaml

    schedule_path = _resolve_schedule_path()
    if not schedule_path.exists():
        return {"enabled": False}
    with schedule_path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _setup_logging() -> logging.Logger:
    log_path = _resolve_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("offmarket_scheduler")
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        fh = logging.FileHandler(log_path, encoding="utf-8")
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
    state = load_offmarket_state(_resolve_state_path())

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
        save_offmarket_state(_resolve_state_path(), state)
        logger.info("Offmarket maintenance completed with status=%s", result.get("status"))
    except Exception as exc:
        state.last_run_utc = now
        state.last_run_result = f"error: {exc}"
        save_offmarket_state(_resolve_state_path(), state)
        logger.error("Offmarket maintenance failed: %s", exc)


if __name__ == "__main__":
    main()
