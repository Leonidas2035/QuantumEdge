from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, Mapping

import yaml


def load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config must be a mapping: {path}")
    return data


def merge_defaults(base: Mapping[str, Any], overrides: Mapping[str, Any]) -> Dict[str, Any]:
    merged: Dict[str, Any] = dict(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_defaults(merged[key], value)
        else:
            merged[key] = value
    return merged


def validate_required(data: Mapping[str, Any], required_paths: Iterable[str]) -> None:
    missing = []
    for path in required_paths:
        node: Any = data
        ok = True
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                ok = False
                break
            node = node[part]
        if not ok:
            missing.append(path)
    if missing:
        raise ValueError(f"Missing required config fields: {', '.join(missing)}")
