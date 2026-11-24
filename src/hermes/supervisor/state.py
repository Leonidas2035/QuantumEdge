"""Helpers for persisting runtime state under the state/ directory."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional, TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hermes.supervisor.process_manager import ProcessInfo


STATE_FILENAME = "process_state.json"
RISK_STATE_FILENAME = "risk_state.json"
META_SUPERVISOR_STATE_FILENAME = "meta_supervisor_state.json"


def _state_file(state_dir: Path) -> Path:
    return state_dir / STATE_FILENAME


def load_process_info(state_dir: Path) -> Optional["ProcessInfo"]:
    """Load process info from disk if present."""

    path = _state_file(state_dir)
    if not path.exists():
        return None

    try:
        with path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (json.JSONDecodeError, OSError) as exc:
        logging.getLogger(__name__).warning("Failed to read process state: %s", exc)
        return None

    try:
        from hermes.supervisor.process_manager import ProcessInfo
    except Exception:
        return None

    start_time_str = raw.get("start_time")
    last_exit_time_str = raw.get("last_exit_time")
    start_time = datetime.fromisoformat(start_time_str) if start_time_str else None
    last_exit_time = (
        datetime.fromisoformat(last_exit_time_str) if last_exit_time_str else None
    )

    try:
        return ProcessInfo(
            pid=int(raw["pid"]),
            start_time=start_time,
            last_exit_code=raw.get("last_exit_code"),
            last_exit_time=last_exit_time,
        )
    except Exception as exc:
        logging.getLogger(__name__).warning("Invalid process state content: %s", exc)
        return None


def save_process_info(state_dir: Path, info: "ProcessInfo") -> None:
    """Persist process info to disk."""

    state_dir.mkdir(parents=True, exist_ok=True)
    path = _state_file(state_dir)
    payload = {
        "pid": info.pid,
        "start_time": info.start_time.isoformat() if info.start_time else None,
        "last_exit_code": info.last_exit_code,
        "last_exit_time": (
            info.last_exit_time.isoformat() if info.last_exit_time else None
        ),
    }
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)


def clear_process_info(state_dir: Path) -> None:
    """Remove persisted process info."""

    path = _state_file(state_dir)
    if path.exists():
        try:
            path.unlink()
        except OSError as exc:
            logging.getLogger(__name__).warning(
                "Failed to remove process state: %s", exc
            )


@dataclass
class MetaSupervisorState:
    """Tracks Meta-Agent supervisor runs."""

    last_run_at: Optional[datetime]
    last_status: Optional[str]
    last_reason: Optional[str]
    last_reports: list
    last_run_mode: Optional[str]


def load_meta_supervisor_state(state_path: Path) -> MetaSupervisorState:
    """Load meta supervisor state from disk."""

    if not state_path.exists():
        return MetaSupervisorState(
            last_run_at=None,
            last_status=None,
            last_reason=None,
            last_reports=[],
            last_run_mode=None,
        )

    try:
        with state_path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (json.JSONDecodeError, OSError) as exc:
        logging.getLogger(__name__).warning(
            "Failed to read meta supervisor state: %s", exc
        )
        return MetaSupervisorState(
            last_run_at=None,
            last_status=None,
            last_reason=None,
            last_reports=[],
            last_run_mode=None,
        )

    last_run_at_str = raw.get("last_run_at")
    try:
        last_run_at = (
            datetime.fromisoformat(last_run_at_str) if last_run_at_str else None
        )
    except ValueError:
        last_run_at = None

    return MetaSupervisorState(
        last_run_at=last_run_at,
        last_status=raw.get("last_status"),
        last_reason=raw.get("last_reason"),
        last_reports=raw.get("last_reports") or [],
        last_run_mode=raw.get("last_run_mode"),
    )


def save_meta_supervisor_state(
    state_path: Path, meta_state: MetaSupervisorState
) -> None:
    """Persist meta supervisor state."""

    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_run_at": (
            meta_state.last_run_at.isoformat() if meta_state.last_run_at else None
        ),
        "last_status": meta_state.last_status,
        "last_reason": meta_state.last_reason,
        "last_reports": meta_state.last_reports or [],
        "last_run_mode": meta_state.last_run_mode,
    }
    with state_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)


import decimal
from decimal import Decimal


@dataclass(slots=True, frozen=False)
class RiskStateSnapshot:
    """Captures rolling risk metrics for the current trading day."""

    trading_day: date
    equity_start: Optional[Decimal]
    equity_now: Optional[Decimal]
    realized_pnl_today: Optional[Decimal]
    max_equity_intraday: Optional[Decimal]
    min_equity_intraday: Decimal
    halted: bool
    halt_reason: Optional[str]
    llm_risk_multiplier: float = 1.0
    llm_paused: bool = False
    llm_last_action: Optional[str] = None
    llm_last_reason: Optional[str] = None
    # ── Phase 1: State Analysis fields ───────────────────────────
    total_equity: Optional[Decimal] = None
    free_margin: Optional[Decimal] = None
    unrealized_pnl: Optional[Decimal] = None
    open_positions_count: int = 0
    portfolio_skew: float = 0.0
    current_leverage: float = 0.0


def _risk_state_file(state_dir: Path) -> Path:
    return state_dir / RISK_STATE_FILENAME


def load_risk_state(state_dir: Path, today: date) -> RiskStateSnapshot:
    """Load risk state; reset when a new trading day starts."""

    path = _risk_state_file(state_dir)

    def _to_decimal(val: Any) -> Optional[Decimal]:
        return Decimal(str(val)) if val is not None else None

    if not path.exists():
        return RiskStateSnapshot(
            trading_day=today,
            equity_start=None,
            equity_now=None,
            realized_pnl_today=None,
            max_equity_intraday=None,
            min_equity_intraday=Decimal("0.0"),
            halted=False,
            halt_reason=None,
            llm_risk_multiplier=1.0,
            llm_paused=False,
            llm_last_action=None,
            llm_last_reason=None,
        )

    try:
        with path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (json.JSONDecodeError, OSError) as exc:
        logging.getLogger(__name__).warning("Failed to read risk state: %s", exc)
        return RiskStateSnapshot(
            trading_day=today,
            equity_start=None,
            equity_now=None,
            realized_pnl_today=None,
            max_equity_intraday=None,
            min_equity_intraday=Decimal("0.0"),
            halted=False,
            halt_reason=None,
            llm_risk_multiplier=1.0,
            llm_paused=False,
            llm_last_action=None,
            llm_last_reason=None,
        )

    raw_day = raw.get("trading_day")
    try:
        stored_day = datetime.fromisoformat(raw_day).date() if raw_day else None
    except ValueError:
        stored_day = None
    if stored_day != today:
        return RiskStateSnapshot(
            trading_day=today,
            equity_start=None,
            equity_now=None,
            realized_pnl_today=None,
            max_equity_intraday=None,
            min_equity_intraday=Decimal("0.0"),
            halted=False,
            halt_reason=None,
        )

    return RiskStateSnapshot(
        trading_day=today,
        equity_start=_to_decimal(raw.get("equity_start")),
        equity_now=_to_decimal(raw.get("equity_now")),
        realized_pnl_today=_to_decimal(raw.get("realized_pnl_today")),
        max_equity_intraday=_to_decimal(raw.get("max_equity_intraday")),
        min_equity_intraday=_to_decimal(raw.get("min_equity_intraday"))
        or Decimal("0.0"),
        halted=bool(raw.get("halted", False)),
        halt_reason=raw.get("halt_reason"),
        llm_risk_multiplier=float(raw.get("llm_risk_multiplier", 1.0)),
        llm_paused=bool(raw.get("llm_paused", False)),
        llm_last_action=raw.get("llm_last_action"),
        llm_last_reason=raw.get("llm_last_reason"),
        total_equity=_to_decimal(raw.get("total_equity")),
        free_margin=_to_decimal(raw.get("free_margin")),
        unrealized_pnl=_to_decimal(raw.get("unrealized_pnl")),
        open_positions_count=int(raw.get("open_positions_count", 0)),
        portfolio_skew=float(raw.get("portfolio_skew", 0.0)),
        current_leverage=float(raw.get("current_leverage", 0.0)),
    )


def save_risk_state(state_dir: Path, snapshot: RiskStateSnapshot) -> None:
    """Persist risk state to disk."""

    state_dir.mkdir(parents=True, exist_ok=True)
    path = _risk_state_file(state_dir)

    def _to_float(val: Optional[Decimal]) -> Optional[float]:
        return float(val) if val is not None else None

    payload = {
        "trading_day": snapshot.trading_day.isoformat(),
        "equity_start": _to_float(snapshot.equity_start),
        "equity_now": _to_float(snapshot.equity_now),
        "realized_pnl_today": _to_float(snapshot.realized_pnl_today),
        "max_equity_intraday": _to_float(snapshot.max_equity_intraday),
        "min_equity_intraday": float(snapshot.min_equity_intraday),
        "halted": snapshot.halted,
        "halt_reason": snapshot.halt_reason,
        "llm_risk_multiplier": snapshot.llm_risk_multiplier,
        "llm_paused": snapshot.llm_paused,
        "llm_last_action": snapshot.llm_last_action,
        "llm_last_reason": snapshot.llm_last_reason,
        "total_equity": _to_float(snapshot.total_equity),
        "free_margin": _to_float(snapshot.free_margin),
        "unrealized_pnl": _to_float(snapshot.unrealized_pnl),
        "open_positions_count": snapshot.open_positions_count,
        "portfolio_skew": snapshot.portfolio_skew,
        "current_leverage": snapshot.current_leverage,
    }
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
