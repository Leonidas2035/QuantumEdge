"""Alert engine with rule evaluation, cooldown, and persistence."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from supervisor.alerts.rules import AlertRule
from supervisor.alerts.storage import AlertRecord, AlertStorage


@dataclass
class AlertResult:
    active: List[Dict[str, Any]]
    recent: List[Dict[str, Any]]


class AlertEngine:
    def __init__(self, rules: List[AlertRule], storage: AlertStorage) -> None:
        self.rules = rules
        self.storage = storage
        self._last_fired: Dict[str, float] = {}

    def evaluate(
        self, summary: Dict[str, Any], *, now: Optional[float] = None
    ) -> AlertResult:
        now = now if now is not None else time.time()
        active = self.storage.load_active()
        silenced = self._active_silences(now)
        for rule in self.rules:
            if rule.name in silenced:
                continue
            match = _eval_rule(summary, rule)
            key = rule.name
            existing = next(
                (alert for alert in active.values() if alert.rule == key), None
            )
            if match:
                if existing is None:
                    record = AlertRecord(
                        alert_id=str(uuid.uuid4()),
                        rule=rule.name,
                        severity=rule.severity,
                        message=match["message"],
                        first_seen=now,
                        last_seen=now,
                        active=False,
                        acknowledged=False,
                        ack_note=None,
                        evidence=match["evidence"],
                        clear_since=None,
                    )
                    active[record.alert_id] = record
                    existing = record
                existing.last_seen = now
                existing.message = match["message"]
                existing.evidence = match["evidence"]
                existing.clear_since = None
                if now - existing.first_seen >= rule.duration_sec:
                    if not existing.active:
                        existing.active = True
                        if self._cooldown_ok(rule.name, now, rule.cooldown_sec):
                            self.storage.append_history(
                                {
                                    "ts": now,
                                    "type": "ALERT_RAISED",
                                    "alert_id": existing.alert_id,
                                    "rule": rule.name,
                                    "severity": rule.severity,
                                    "message": existing.message,
                                }
                            )
                    active[existing.alert_id] = existing
            else:
                if existing:
                    if existing.clear_since is None:
                        existing.clear_since = now
                    if (
                        existing.active
                        and now - existing.clear_since >= rule.resolve_after_sec
                    ):
                        existing.active = False
                        existing.last_seen = now
                        self.storage.append_history(
                            {
                                "ts": now,
                                "type": "ALERT_RESOLVED",
                                "alert_id": existing.alert_id,
                                "rule": rule.name,
                                "message": existing.message,
                            }
                        )
                        active.pop(existing.alert_id, None)
        self.storage.save_active(active)
        return AlertResult(
            active=[alert.to_dict() for alert in active.values() if alert.active],
            recent=self.storage.recent_history(limit=200),
        )

    def ack(self, alert_id: str, note: str) -> bool:
        return self.storage.ack(alert_id, note)

    def silence(self, rule: str, minutes: int) -> float:
        return self.storage.silence(rule, minutes)

    def _cooldown_ok(self, rule: str, now: float, cooldown: int) -> bool:
        last = self._last_fired.get(rule, 0.0)
        if now - last < cooldown:
            return False
        self._last_fired[rule] = now
        return True

    def _active_silences(self, now: float) -> Dict[str, float]:
        silences = self.storage.load_silences()
        return {rule: until for rule, until in silences.items() if until > now}


def _eval_rule(summary: Dict[str, Any], rule: AlertRule) -> Dict[str, Any] | None:
    value = _get_field(summary, rule.field)
    op = rule.operator
    threshold = rule.threshold
    if op == "truthy":
        if value:
            return {"message": f"{rule.field} truthy", "evidence": {"value": value}}
        return None
    try:
        val_f = float(value)
    except (TypeError, ValueError):
        return None
    if _compare(val_f, threshold, op):
        return {
            "message": f"{rule.field} {op} {threshold} (value={val_f})",
            "evidence": {"value": val_f, "threshold": threshold},
        }
    return None


def _get_field(summary: Dict[str, Any], field: str) -> Any:
    current = summary
    for token in field.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(token)
    return current


def _compare(value: float, threshold: float, op: str) -> bool:
    if op == ">":
        return value > threshold
    if op == ">=":
        return value >= threshold
    if op == "<":
        return value < threshold
    if op == "<=":
        return value <= threshold
    if op == "==":
        return value == threshold
    if op == "!=":
        return value != threshold
    return False
