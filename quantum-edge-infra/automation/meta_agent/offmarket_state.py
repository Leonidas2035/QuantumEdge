from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


@dataclass
class OffmarketState:
    """Tracks off-market maintenance cadence."""

    last_run_utc: Optional[datetime]
    runs_today: int
    last_run_result: Optional[str]


def load_offmarket_state(path: Path) -> OffmarketState:
    if not path.exists():
        return OffmarketState(last_run_utc=None, runs_today=0, last_run_result=None)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return OffmarketState(last_run_utc=None, runs_today=0, last_run_result=None)

    ts_raw = raw.get("last_run_utc")
    ts = None
    try:
        if ts_raw:
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
    except Exception:
        ts = None

    return OffmarketState(
        last_run_utc=ts,
        runs_today=int(raw.get("runs_today", 0)),
        last_run_result=raw.get("last_run_result"),
    )


def save_offmarket_state(path: Path, state: OffmarketState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_run_utc": state.last_run_utc.isoformat() if state.last_run_utc else None,
        "runs_today": state.runs_today,
        "last_run_result": state.last_run_result,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
