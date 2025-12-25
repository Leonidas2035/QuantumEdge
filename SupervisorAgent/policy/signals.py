"""Policy signals collection."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass
class Signals:
    bot_running: bool
    restart_rate: Optional[float]
    pnl_day: Optional[float]
    drawdown_day: Optional[float]
    loss_streak: Optional[int]
    error_rate: Optional[float]
    spread_bps: Optional[float]
    volatility: Optional[float]
    risk_halted: bool
    risk_halt_reason: Optional[str]
    evidence: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bot_running": self.bot_running,
            "restart_rate": self.restart_rate,
            "pnl_day": self.pnl_day,
            "drawdown_day": self.drawdown_day,
            "loss_streak": self.loss_streak,
            "error_rate": self.error_rate,
            "spread_bps": self.spread_bps,
            "volatility": self.volatility,
            "risk_halted": self.risk_halted,
            "risk_halt_reason": self.risk_halt_reason,
        }


def _safe_read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _parse_last_exit_time(payload: Dict[str, Any]) -> Optional[float]:
    raw = payload.get("last_exit_time")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.timestamp()
    except Exception:
        return None


def collect_signals(paths, process_manager, risk_engine, logger) -> Signals:
    evidence: Dict[str, Any] = {}
    status = process_manager.get_status_payload()
    bot_running = status.get("state") == "RUNNING"
    restarts = int(status.get("restarts") or 0)
    last_exit_ts = _parse_last_exit_time(status) if isinstance(status, dict) else None
    restart_rate = None
    if restarts > 0 and last_exit_ts:
        hours = max((time.time() - last_exit_ts) / 3600.0, 0.01)
        restart_rate = restarts / hours
    evidence["restarts"] = restarts
    evidence["last_exit_ts"] = last_exit_ts

    pnl_day = None
    drawdown_day = None
    risk_halted = False
    risk_halt_reason = None
    try:
        snapshot = risk_engine.get_state()
        risk_halted = bool(snapshot.halted)
        risk_halt_reason = snapshot.halt_reason
        if snapshot.realized_pnl_today is not None:
            pnl_day = float(snapshot.realized_pnl_today)
        if snapshot.equity_start is not None and snapshot.equity_now is not None and pnl_day is None:
            pnl_day = float(snapshot.equity_now - snapshot.equity_start)
        if snapshot.max_equity_intraday is not None and snapshot.equity_now is not None:
            drawdown_day = float(snapshot.max_equity_intraday - snapshot.equity_now)
    except Exception as exc:
        logger.debug("Signals: risk state unavailable: %s", exc)

    status_path = paths.runtime_dir / "bot_status.json"
    bot_status = _safe_read_json(status_path)
    if isinstance(bot_status, dict):
        evidence["bot_status_ts"] = bot_status.get("ts")
        if pnl_day is None and bot_status.get("total_pnl") is not None:
            try:
                pnl_day = float(bot_status.get("total_pnl"))
            except Exception:
                pass
        if drawdown_day is None and bot_status.get("max_drawdown_abs") is not None:
            try:
                drawdown_day = float(bot_status.get("max_drawdown_abs"))
            except Exception:
                pass

    return Signals(
        bot_running=bool(bot_running),
        restart_rate=restart_rate,
        pnl_day=pnl_day,
        drawdown_day=drawdown_day,
        loss_streak=None,
        error_rate=None,
        spread_bps=None,
        volatility=None,
        risk_halted=risk_halted,
        risk_halt_reason=risk_halt_reason,
        evidence=evidence,
    )
