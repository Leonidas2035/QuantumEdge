"""Alert storage with ack/silence and history persistence."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class AlertRecord:
    alert_id: str
    rule: str
    severity: str
    message: str
    first_seen: float
    last_seen: float
    active: bool
    acknowledged: bool
    ack_note: Optional[str]
    evidence: Dict[str, Any]
    clear_since: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "alert_id": self.alert_id,
            "rule": self.rule,
            "severity": self.severity,
            "message": self.message,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "active": self.active,
            "acknowledged": self.acknowledged,
            "ack_note": self.ack_note,
            "evidence": self.evidence,
            "clear_since": self.clear_since,
        }


class AlertStorage:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.history_path = self.base_dir / "alerts.jsonl"
        self.active_path = self.base_dir / "active.json"
        self.silence_path = self.base_dir / "silence.json"

    def load_active(self) -> Dict[str, AlertRecord]:
        if not self.active_path.exists():
            return {}
        try:
            payload = json.loads(self.active_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        result: Dict[str, AlertRecord] = {}
        for alert in payload.get("alerts", []) or []:
            if not isinstance(alert, dict):
                continue
            record = AlertRecord(
                alert_id=str(alert.get("alert_id", "")),
                rule=str(alert.get("rule", "")),
                severity=str(alert.get("severity", "WARN")),
                message=str(alert.get("message", "")),
                first_seen=float(alert.get("first_seen", 0)),
                last_seen=float(alert.get("last_seen", 0)),
                active=bool(alert.get("active", False)),
                acknowledged=bool(alert.get("acknowledged", False)),
                ack_note=alert.get("ack_note"),
                evidence=alert.get("evidence", {}) if isinstance(alert.get("evidence"), dict) else {},
                clear_since=alert.get("clear_since"),
            )
            if record.alert_id:
                result[record.alert_id] = record
        return result

    def save_active(self, alerts: Dict[str, AlertRecord]) -> None:
        payload = {"alerts": [alert.to_dict() for alert in alerts.values()]}
        self.active_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def append_history(self, event: Dict[str, Any]) -> None:
        with self.history_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event) + "\n")

    def recent_history(self, limit: int = 200) -> List[Dict[str, Any]]:
        if not self.history_path.exists():
            return []
        try:
            lines = self.history_path.read_text(encoding="utf-8").splitlines()
        except Exception:
            return []
        items: List[Dict[str, Any]] = []
        for line in lines[-limit:]:
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return items

    def load_silences(self) -> Dict[str, float]:
        if not self.silence_path.exists():
            return {}
        try:
            payload = json.loads(self.silence_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        silences = {}
        for rule, until in (payload.get("rules") or {}).items():
            try:
                silences[str(rule)] = float(until)
            except (TypeError, ValueError):
                continue
        return silences

    def save_silences(self, silences: Dict[str, float]) -> None:
        payload = {"rules": silences}
        self.silence_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def ack(self, alert_id: str, note: str) -> bool:
        alerts = self.load_active()
        record = alerts.get(alert_id)
        if not record:
            return False
        record.acknowledged = True
        record.ack_note = note
        record.last_seen = time.time()
        alerts[alert_id] = record
        self.save_active(alerts)
        self.append_history({"ts": time.time(), "type": "ALERT_ACK", "alert_id": alert_id, "note": note})
        return True

    def silence(self, rule: str, minutes: int) -> float:
        now = time.time()
        until = now + max(minutes, 1) * 60
        silences = self.load_silences()
        silences[rule] = until
        self.save_silences(silences)
        self.append_history({"ts": now, "type": "ALERT_SILENCE", "rule": rule, "until": until})
        return until
