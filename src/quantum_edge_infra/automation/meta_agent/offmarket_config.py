import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import yaml

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

try:
    from tools.qe_config import get_qe_paths
except Exception:  # pragma: no cover - fallback for legacy runs
    get_qe_paths = None

DEFAULT_CONFIG_PATH = os.path.join("config", "offmarket_schedule.yaml")
DEFAULT_STATE_PATH = os.path.join("state", "offmarket_state.json")


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


def _resolve_base_dir() -> str:
    env_root = os.getenv("QE_ROOT")
    if env_root:
        return env_root
    if get_qe_paths:
        try:
            return str(get_qe_paths()["qe_root"])
        except Exception:
            pass
    parent = os.path.abspath(os.path.join(BASE_DIR, os.pardir))
    if os.path.isdir(os.path.join(parent, "config")) and os.path.isdir(os.path.join(parent, "ai_scalper_bot")):
        return parent
    return BASE_DIR


def _resolve_config_path(path: Optional[str]) -> str:
    base = _resolve_base_dir()
    candidate = path or DEFAULT_CONFIG_PATH
    if os.path.isabs(candidate):
        return candidate
    return os.path.abspath(os.path.join(base, candidate))


def _resolve_state_path(path: Optional[str]) -> str:
    runtime_dir = os.getenv("QE_RUNTIME_DIR")
    if runtime_dir:
        return os.path.abspath(os.path.join(runtime_dir, "meta_agent", "offmarket_state.json"))
    base = _resolve_base_dir()
    candidate = path or DEFAULT_STATE_PATH
    if os.path.isabs(candidate):
        return candidate
    return os.path.abspath(os.path.join(base, candidate))


def _load_meta_agent_defaults() -> Dict[str, str]:
    base = _resolve_base_dir()
    cfg_path = os.getenv("META_AGENT_CONFIG") or os.path.join(base, "config", "meta_agent.yaml")
    if not os.path.exists(cfg_path):
        return {}
    try:
        with open(cfg_path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except yaml.YAMLError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: str(v) for k, v in data.items() if isinstance(v, str)}


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
    env_override = os.getenv("META_AGENT_OFFMARKET_CONFIG")
    defaults = _load_meta_agent_defaults()
    effective_path = env_override or defaults.get("offmarket_schedule_path") or path
    resolved_path = _resolve_config_path(effective_path)

    if not os.path.exists(resolved_path):
        raise FileNotFoundError(f"Off-market config not found: {resolved_path}")

    with open(resolved_path, "r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    project = raw.get("project", "ai_scalper_bot")
    timezone = raw.get("timezone", "UTC")
    cooldown_minutes = int(raw.get("cooldown_minutes", 720))
    max_runs_per_day = int(raw.get("max_runs_per_day", 1))
    require_bot_idle = bool(raw.get("require_bot_idle", True))
    bot_status_file = raw.get("bot_status_file")
    if bot_status_file and not os.path.isabs(bot_status_file):
        bot_status_file = os.path.abspath(os.path.join(_resolve_base_dir(), bot_status_file))

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
    resolved_path = _resolve_state_path(path)
    if not os.path.exists(resolved_path):
        return OffMarketState()
    try:
        with open(resolved_path, "r", encoding="utf-8") as handle:
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
    resolved_path = _resolve_state_path(path)
    _ensure_dir(os.path.dirname(resolved_path))
    with open(resolved_path, "w", encoding="utf-8") as handle:
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
