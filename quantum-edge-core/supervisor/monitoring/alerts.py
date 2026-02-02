"""Alert rules with cooldown for telemetry monitoring."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass
class Alert:
    key: str
    severity: str
    message: str
    first_seen: float
    last_seen: float
    active: bool
    evidence: Dict[str, object]

    def to_dict(self) -> Dict[str, object]:
        return {
            "key": self.key,
            "severity": self.severity,
            "message": self.message,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "active": self.active,
            "evidence": self.evidence,
        }


class AlertManager:
    def __init__(self, thresholds: Dict[str, object], cooldown_sec: int = 120) -> None:
        self.thresholds = thresholds
        self.cooldown_sec = max(1, int(cooldown_sec))
        self._last_fired: Dict[str, float] = {}
        self._alerts: Dict[str, Alert] = {}
        self._history: List[Alert] = []

    def evaluate(self, summary: Dict[str, object]) -> None:
        now = time.time()
        conditions: Dict[str, Tuple[str, str, Dict[str, object]]] = {}

        restart_rate = _safe_float(summary.get("restart_rate_per_hour"))
        restart_threshold = _safe_float(self.thresholds.get("restart_rate_per_hour"))
        if restart_rate is not None and restart_threshold is not None and restart_rate >= restart_threshold:
            conditions["BOT_RESTART_LOOP"] = (
                "high",
                f"Restart rate {restart_rate:.2f}/h >= {restart_threshold:.2f}/h",
                {"restart_rate_per_hour": restart_rate},
            )

        error_rate = _safe_float(summary.get("error_rate_1m"))
        error_threshold = _safe_float(self.thresholds.get("error_rate_1m"))
        if error_rate is not None and error_threshold is not None and error_rate >= error_threshold:
            conditions["ERROR_SPIKE"] = (
                "high",
                f"Errors/min {int(error_rate)} >= {int(error_threshold)}",
                {"error_rate_1m": error_rate},
            )

        latency_p95 = _safe_float(summary.get("latency_ms_p95"))
        latency_threshold = _safe_float(self.thresholds.get("latency_ms"))
        if latency_p95 is not None and latency_threshold is not None and latency_p95 >= latency_threshold:
            conditions["LATENCY_SPIKE"] = (
                "medium",
                f"Latency p95 {latency_p95:.1f}ms >= {latency_threshold:.1f}ms",
                {"latency_ms_p95": latency_p95},
            )

        drawdown = _safe_float(summary.get("drawdown_day"))
        drawdown_threshold = _safe_float(self.thresholds.get("drawdown_abs"))
        if drawdown is not None and drawdown_threshold is not None and drawdown >= drawdown_threshold:
            conditions["DRAWDOWN_LIMIT"] = (
                "high",
                f"Drawdown {drawdown:.2f} >= {drawdown_threshold:.2f}",
                {"drawdown_day": drawdown},
            )

        pnl_day = _safe_float(summary.get("pnl_day"))
        max_daily_loss = _safe_float(self.thresholds.get("max_daily_loss"))
        if pnl_day is not None and max_daily_loss is not None and pnl_day <= -abs(max_daily_loss):
            conditions["DRAWDOWN_LIMIT"] = (
                "high",
                f"PnL {pnl_day:.2f} <= -{abs(max_daily_loss):.2f}",
                {"pnl_day": pnl_day},
            )

        policy_allow = summary.get("policy_allow_trading")
        policy_reason = str(summary.get("policy_reason") or "")
        if policy_allow is False and _policy_safe_reason(policy_reason):
            conditions["POLICY_SAFE_MODE_ACTIVE"] = (
                "medium",
                "Policy in safe mode (missing/expired)",
                {"policy_reason": policy_reason},
            )

        active_keys = set(conditions.keys())
        for key, (severity, message, evidence) in conditions.items():
            self._upsert_alert(key, severity, message, evidence, now)

        for key, alert in list(self._alerts.items()):
            if key not in active_keys and alert.active:
                alert.active = False
                alert.last_seen = now
                self._history.append(alert)

    def _upsert_alert(self, key: str, severity: str, message: str, evidence: Dict[str, object], now: float) -> None:
        last_fired = self._last_fired.get(key, 0.0)
        alert = self._alerts.get(key)
        if alert is None:
            alert = Alert(key=key, severity=severity, message=message, first_seen=now, last_seen=now, active=True, evidence=evidence)
            self._alerts[key] = alert
            if now - last_fired >= self.cooldown_sec:
                self._last_fired[key] = now
                self._history.append(alert)
            return
        alert.last_seen = now
        alert.active = True
        alert.severity = severity
        alert.message = message
        alert.evidence = evidence
        if now - last_fired >= self.cooldown_sec:
            self._last_fired[key] = now
            self._history.append(alert)

    def active_alerts(self) -> List[Dict[str, object]]:
        return [alert.to_dict() for alert in self._alerts.values() if alert.active]

    def recent_alerts(self, limit: int = 100) -> List[Dict[str, object]]:
        return [alert.to_dict() for alert in self._history[-limit:]]


def _safe_float(value: object) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _policy_safe_reason(reason: str) -> bool:
    reason = reason.upper()
    tokens = ["POLICY_MISSING", "POLICY_EXPIRED", "POLICY_NOT_READY", "POLICY_MISSING_OR_EXPIRED"]
    return any(token in reason for token in tokens)
