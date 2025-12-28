"""Best-effort state recovery helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from bot.state.store import StateStore


def recover_state(base_dir: Path) -> Dict[str, Dict[str, Any]]:
    store = StateStore(base_dir)
    position_state = store.load_position_state()
    orders_state = store.load_orders_state()
    return {"position": position_state, "orders": orders_state}
