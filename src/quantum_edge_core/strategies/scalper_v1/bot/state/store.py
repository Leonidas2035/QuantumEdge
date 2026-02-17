"""Crash-safe state persistence for orders and positions."""

from __future__ import annotations

import hashlib
import json
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict


def _atomic_write(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=path.stem + "_", suffix=".json", dir=str(path.parent)
    )
    try:
        with open(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        Path(tmp_path).replace(path)
    finally:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            pass


@dataclass
class OrderRecord:
    client_order_id: str
    symbol: str
    side: str
    size: float
    price: float
    status: str
    created_ts: int


class StateStore:
    def __init__(self, base_dir: Path, max_recent_ids: int = 1000) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.position_path = self.base_dir / "position_state.json"
        self.orders_path = self.base_dir / "orders_state.json"
        self.ledger_path = self.base_dir / "ledger.jsonl"
        self.max_recent_ids = max(int(max_recent_ids), 100)

    def load_position_state(self) -> Dict[str, Any]:
        if not self.position_path.exists():
            return {
                "schema_version": "v1",
                "position": 0.0,
                "entry_price": None,
                "updated_ts": None,
            }
        try:
            return json.loads(self.position_path.read_text(encoding="utf-8"))
        except Exception:
            return {
                "schema_version": "v1",
                "position": 0.0,
                "entry_price": None,
                "updated_ts": None,
            }

    def save_position_state(self, data: Dict[str, Any]) -> None:
        payload = dict(data)
        payload.setdefault("schema_version", "v1")
        payload["updated_ts"] = int(time.time())
        _atomic_write(self.position_path, payload)

    def load_orders_state(self) -> Dict[str, Any]:
        if not self.orders_path.exists():
            return {"schema_version": "v1", "orders": {}, "recent_order_ids": []}
        try:
            return json.loads(self.orders_path.read_text(encoding="utf-8"))
        except Exception:
            return {"schema_version": "v1", "orders": {}, "recent_order_ids": []}

    def save_orders_state(self, data: Dict[str, Any]) -> None:
        payload = dict(data)
        payload.setdefault("schema_version", "v1")
        _atomic_write(self.orders_path, payload)

    def append_ledger(self, event: Dict[str, Any]) -> None:
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.ledger_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event) + "\n")
        except Exception:
            return

    def generate_client_order_id(
        self, symbol: str, side: str, action: str, ts_ms: int, size: float
    ) -> str:
        bucket = int(ts_ms // 1000)
        base = f"{symbol}|{side}|{action}|{bucket}|{size:.6f}"
        digest = hashlib.sha1(base.encode("utf-8")).hexdigest()[:10]
        return f"qe_{symbol.lower()}_{digest}"

    def is_duplicate(self, client_order_id: str) -> bool:
        state = self.load_orders_state()
        recent = set(state.get("recent_order_ids", []) or [])
        return client_order_id in recent

    def record_order(self, record: OrderRecord) -> None:
        state = self.load_orders_state()
        orders = state.get("orders", {}) or {}
        orders[record.client_order_id] = {
            "symbol": record.symbol,
            "side": record.side,
            "size": record.size,
            "price": record.price,
            "status": record.status,
            "created_ts": record.created_ts,
        }
        recent = list(state.get("recent_order_ids", []) or [])
        recent.append(record.client_order_id)
        if len(recent) > self.max_recent_ids:
            recent = recent[-self.max_recent_ids :]
        state["orders"] = orders
        state["recent_order_ids"] = recent
        self.save_orders_state(state)
