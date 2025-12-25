import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import yaml

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DEFAULT_CONFIG_PATH = os.path.join(BASE_DIR, "config", "offmarket_schedule.yaml")
DEFAULT_STATE_PATH = os.path.join(BASE_DIR, "state", "offmarket_state.json")


@dataclass
class OffMarketScheduleItem:
    goal: str
    mode: str
    enabled: bool
    days: List[str]
    time: str
    window_minutes: int = 60


@dataclass
class OffMarketConfig:
    project: str
    timezone: str
    cooldown_minutes: int
    max_runs_per_day: int
    require_bot_idle: bool
    bot_status_file: Optional[str]
    schedules: List[OffMarketScheduleItem] = field(default_factory=list)


@dataclass
class OffMarketState:
    last_runs: Dict[str, str] = field(default_factory=dict)   # goal -> ISO ts
    runs_today: Dict[str, int] = field(default_factory=dict)  # goal -> count
    runs_date: Optional[str] = None                           # YYYY-MM-DD in config timezone


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _normalize_days(days: List[str]) -> List[str]:
    if not days:
        return ["*"]
    normalized = []
    for d in days:
        normalized.append(d.strip().title())
    return normalized or ["*"]


def load_offmarket_config(path: str = DEFAULT_CONFIG_PATH) -> OffMarketConfig:
    """
    Loads off-market schedule config from YAML and returns an OffMarketConfig dataclass.
    Applies reasonable defaults for missing fields.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"Off-market config not found: {path}")

    with open(path, "r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    project = raw.get("project", "ai_scalper_bot")
    timezone = raw.get("timezone", "UTC")
    cooldown_minutes = int(raw.get("cooldown_minutes", 720))
    max_runs_per_day = int(raw.get("max_runs_per_day", 1))
    require_bot_idle = bool(raw.get("require_bot_idle", True))
    bot_status_file = raw.get("bot_status_file")

    schedules_raw = raw.get("schedules") or []
    schedules: List[OffMarketScheduleItem] = []
    for item in schedules_raw:
        if not isinstance(item, dict):
            continue
        schedules.append(
            OffMarketScheduleItem(
                goal=item.get("goal", ""),
                mode=item.get("mode", "daily"),
                enabled=bool(item.get("enabled", True)),
                days=_normalize_days(item.get("days") or ["*"]),
                time=item.get("time", "03:00"),
                window_minutes=int(item.get("window_minutes", 60)),
            )
        )

    if not schedules:
        raise ValueError("Off-market config must define at least one schedule item.")

    return OffMarketConfig(
        project=project,
        timezone=timezone,
        cooldown_minutes=cooldown_minutes,
        max_runs_per_day=max_runs_per_day,
        require_bot_idle=require_bot_idle,
        bot_status_file=bot_status_file,
        schedules=schedules,
    )


def load_offmarket_state(path: str = DEFAULT_STATE_PATH) -> OffMarketState:
    """
    Loads off-market state from JSON. Returns empty state if missing.
    """
    if not os.path.exists(path):
        return OffMarketState()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle) or {}
        return OffMarketState(
            last_runs=data.get("last_runs", {}),
            runs_today=data.get("runs_today", {}),
            runs_date=data.get("runs_date"),
        )
    except (json.JSONDecodeError, OSError):
        return OffMarketState()


def save_offmarket_state(state: OffMarketState, path: str = DEFAULT_STATE_PATH) -> None:
    """
    Writes state to JSON, creating the directory if needed.
    """
    _ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(
            {
                "last_runs": state.last_runs,
                "runs_today": state.runs_today,
                "runs_date": state.runs_date,
            },
            handle,
            ensure_ascii=True,
            indent=2,
        )


def is_bot_idle(config: OffMarketConfig) -> bool:
    """
    Checks bot status file (optional) to ensure the bot is idle before maintenance.
    """
    if not config.require_bot_idle:
        return True
    if not config.bot_status_file:
        return False
    status_path = os.path.abspath(config.bot_status_file)
    if not os.path.exists(status_path):
        return False
    try:
        with open(status_path, "r", encoding="utf-8") as handle:
            data = json.load(handle) or {}
    except (json.JSONDecodeError, OSError):
        return False

    is_trading = data.get("is_trading", True)
    open_positions = data.get("open_positions", 1)
    return (not is_trading) and (open_positions == 0)
