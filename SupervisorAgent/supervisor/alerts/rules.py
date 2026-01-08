"""Alert rule definitions and loader."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

import yaml


@dataclass
class AlertRule:
    name: str
    severity: str
    field: str
    operator: str
    threshold: float
    duration_sec: int
    cooldown_sec: int
    resolve_after_sec: int = 0


def load_alert_rules(path: Path) -> List[AlertRule]:
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rules_raw = data.get("rules", []) if isinstance(data, dict) else []
    rules: List[AlertRule] = []
    for entry in rules_raw:
        if not isinstance(entry, dict):
            continue
        rules.append(
            AlertRule(
                name=str(entry.get("name", "")),
                severity=str(entry.get("severity", "WARN")),
                field=str(entry.get("field", "")),
                operator=str(entry.get("operator", ">=")),
                threshold=float(entry.get("threshold", 0)),
                duration_sec=int(entry.get("duration_sec", 0)),
                cooldown_sec=int(entry.get("cooldown_sec", 60)),
                resolve_after_sec=int(entry.get("resolve_after_sec", 0) or 0),
            )
        )
    return [rule for rule in rules if rule.name and rule.field]
