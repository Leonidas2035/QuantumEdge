from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

import yaml


def find_repo_root(start: Optional[Path] = None) -> Path:
    env_root = os.getenv("QE_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()

    candidate = Path(start) if start else Path(__file__).resolve()
    if candidate.is_file():
        candidate = candidate.parent
    for parent in [candidate] + list(candidate.parents):
        if (parent / ".git").exists():
            return parent.resolve()
        if (parent / "QuantumEdge.py").exists():
            return parent.resolve()
        if (parent / "config" / "quantumedge.yaml").exists():
            return parent.resolve()
    return Path(__file__).resolve().parents[1]


def _load_yaml(path: Path) -> Dict[str, object]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return data if isinstance(data, dict) else {}


def _resolve_path(value: Optional[str], base: Path) -> Optional[Path]:
    if value is None or value == "":
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def get_paths() -> Dict[str, Path]:
    qe_root = find_repo_root()
    defaults: Dict[str, Path] = {
        "qe_root": qe_root,
        "config_dir": qe_root / "config",
        "runtime_dir": qe_root / "runtime",
        "logs_dir": qe_root / "logs",
        "data_dir": qe_root / "data",
        "artifacts_dir": qe_root / "artifacts",
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
        "artifacts_dir": os.getenv("QE_ARTIFACTS_DIR"),
    }
    for key, value in env_overrides.items():
        resolved = _resolve_path(value, qe_root)
        if resolved:
            defaults[key] = resolved

    paths_file = defaults["config_dir"] / "paths.yaml"
    raw = _load_yaml(paths_file)
    if isinstance(raw.get("paths"), dict):
        raw = raw["paths"]
    if isinstance(raw, dict):
        for key in list(defaults.keys()):
            if key not in raw:
                continue
            resolved = _resolve_path(str(raw.get(key)), qe_root)
            if resolved:
                defaults[key] = resolved

    defaults["paths_file"] = paths_file
    return defaults


def ensure_dirs(paths: Dict[str, Path], include_logs: bool = True, include_data: bool = True) -> None:
    keys = ["runtime_dir", "artifacts_dir"]
    if include_logs:
        keys.append("logs_dir")
    if include_data:
        keys.append("data_dir")
    for key in keys:
        Path(paths[key]).mkdir(parents=True, exist_ok=True)
