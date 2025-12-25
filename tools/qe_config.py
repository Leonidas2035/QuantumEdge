from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


def _find_qe_root() -> Path:
    env_root = os.getenv("QE_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()
    return Path(__file__).resolve().parents[1]


def _load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config must be a mapping: {path}")
    return data


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config must be a mapping: {path}")
    return data


def _resolve_path(value: Optional[str | Path], base: Path) -> Optional[Path]:
    if value is None or value == "":
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def _resolve_env_path(env_value: Optional[str], base: Path) -> Optional[Path]:
    if not env_value:
        return None
    return _resolve_path(env_value, base)


def get_qe_paths() -> Dict[str, Path]:
    qe_root = _find_qe_root()
    defaults: Dict[str, Path] = {
        "qe_root": qe_root,
        "config_dir": qe_root / "config",
        "runtime_dir": qe_root / "runtime",
        "logs_dir": qe_root / "logs",
        "data_dir": qe_root / "data",
        "bot_dir": qe_root / "ai_scalper_bot",
        "supervisor_dir": qe_root / "SupervisorAgent",
        "meta_agent_dir": qe_root / "meta_agent",
        "bot_config_dir": qe_root / "ai_scalper_bot" / "config",
        "supervisor_config_dir": qe_root / "SupervisorAgent" / "config",
        "meta_agent_config_dir": qe_root / "meta_agent" / "config",
    }

    env_overrides = {
        "config_dir": os.getenv("QE_CONFIG_DIR"),
        "runtime_dir": os.getenv("QE_RUNTIME_DIR"),
        "logs_dir": os.getenv("QE_LOGS_DIR"),
        "data_dir": os.getenv("QE_DATA_DIR"),
    }
    for key, value in env_overrides.items():
        resolved = _resolve_env_path(value, qe_root)
        if resolved:
            defaults[key] = resolved

    paths_file = defaults["config_dir"] / "paths.yaml"
    if paths_file.exists():
        raw = _load_yaml(paths_file)
        if isinstance(raw.get("paths"), dict):
            raw = raw["paths"]
        for key, default in list(defaults.items()):
            if key not in raw:
                continue
            resolved = _resolve_path(raw.get(key), qe_root)
            if resolved:
                defaults[key] = resolved

    defaults["paths_file"] = paths_file
    return defaults


def get_qe_config() -> Dict[str, Any]:
    paths = get_qe_paths()
    config_path = paths["config_dir"] / "quantumedge.yaml"
    raw: Dict[str, Any] = {}
    if config_path.exists():
        raw = _load_yaml(config_path)

    supervisor_raw = raw.get("supervisor", {}) if isinstance(raw.get("supervisor", {}), dict) else {}
    env_host = os.getenv("SUPERVISOR_HOST") or os.getenv("QE_SUPERVISOR_HOST")
    env_port = os.getenv("SUPERVISOR_PORT") or os.getenv("QE_SUPERVISOR_PORT")
    env_url = os.getenv("SUPERVISOR_URL")

    host = env_host or supervisor_raw.get("host", "127.0.0.1")
    port_raw = env_port or supervisor_raw.get("port", 8765)
    try:
        port = int(port_raw)
    except (TypeError, ValueError):
        port = 8765

    url = env_url or supervisor_raw.get("url") or f"http://{host}:{port}"

    return {
        "config_path": str(config_path),
        "paths": {key: str(val) for key, val in paths.items()},
        "supervisor": {"host": str(host), "port": int(port), "url": str(url)},
    }


def load_config_file(path: str | Path) -> Dict[str, Any]:
    config_path = Path(path)
    if config_path.suffix.lower() in {".yaml", ".yml"}:
        return _load_yaml(config_path)
    if config_path.suffix.lower() == ".json":
        return _load_json(config_path)
    raise ValueError(f"Unsupported config format: {config_path}")


def resolve_config_path(env_var: str, default_relative: str) -> Path:
    paths = get_qe_paths()
    env_value = os.getenv(env_var)
    if env_value:
        resolved = _resolve_env_path(env_value, paths["qe_root"])
        if resolved:
            return resolved
    return (paths["qe_root"] / default_relative).resolve()
