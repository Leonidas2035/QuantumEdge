"""Load ML policy exports for runtime threshold overrides."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from bot.ml.features.builder import schema_hash


@dataclass(frozen=True)
class PolicyOverride:
    horizons: list[int]
    thresholds: Dict[int, float]
    policy_type: str
    path: Path
    schema_hash: Optional[str]
    weights: Optional[Dict[int, float]] = None
    score_threshold: Optional[float] = None


def load_policy(path: Path) -> Optional[PolicyOverride]:
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return None
    horizons = [int(h) for h in raw.get("horizons", [])]
    thresholds_raw = raw.get("thresholds") or {}
    thresholds: Dict[int, float] = {}
    if isinstance(thresholds_raw, dict):
        for key, value in thresholds_raw.items():
            key_str = str(key)
            if key_str.startswith("h"):
                key_str = key_str[1:]
            try:
                thresholds[int(key_str)] = float(value)
            except (TypeError, ValueError):
                continue
    weights_raw = raw.get("weights") or {}
    weights: Optional[Dict[int, float]] = None
    if isinstance(weights_raw, dict):
        weights = {}
        for key, value in weights_raw.items():
            key_str = str(key)
            if key_str.startswith("h"):
                key_str = key_str[1:]
            try:
                weights[int(key_str)] = float(value)
            except (TypeError, ValueError):
                continue
    return PolicyOverride(
        horizons=horizons,
        thresholds=thresholds,
        policy_type=str(raw.get("policy_type", "and_gate")),
        path=path,
        schema_hash=raw.get("schema_hash"),
        weights=weights,
        score_threshold=raw.get("score_threshold"),
    )


def resolve_policy_path(ml_cfg: Dict[str, object], qe_root: Path) -> Optional[Path]:
    raw = ml_cfg.get("policy_path") or ml_cfg.get("policy")
    if not raw:
        return None
    path = Path(str(raw))
    if not path.is_absolute():
        path = (qe_root / path).resolve()
    return path


def load_policy_override(ml_cfg: Dict[str, object], qe_root: Path) -> Optional[PolicyOverride]:
    path = resolve_policy_path(ml_cfg, qe_root)
    if not path:
        return None
    policy = load_policy(path)
    if not policy:
        return None
    expected_hash = schema_hash()
    if policy.schema_hash and policy.schema_hash != expected_hash:
        return None
    return policy
