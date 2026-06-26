"""Ops configuration helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from quantum_edge_infra.tools.qe_config_loader import load_yaml


def load_ops_config(config_dir: Path) -> Dict[str, Any]:
    path = config_dir / "ops.yaml"
    try:
        return load_yaml(path)
    except FileNotFoundError:
        return {}


def get_nested(cfg: Mapping[str, Any], path: str, default: Optional[Any] = None) -> Any:
    node: Any = cfg
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node
