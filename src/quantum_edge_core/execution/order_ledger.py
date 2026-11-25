"""Lightweight SQLite Order Ledger for idempotent clientOrderId reservation."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
import sqlite3
from typing import Any


_ACTION_SHORT = {
    "OPEN_LONG": "ol",
    "OPEN_SHORT": "os",
    "CLOSE_LONG": "cl",
    "CLOSE_SHORT": "cs",
    "HOLD": "hd",
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class OrderLedger:
    """Tracks order lifecycle in SQLite to prevent duplicate submissions on retry/timeout."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    client_order_id TEXT PRIMARY KEY,
                    decision_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    action TEXT NOT NULL,
                    status TEXT NOT NULL,
                    exchange_order_id TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.commit()

    @staticmethod
    def make_client_order_id(decision_id: str, symbol: str, action: str) -> str:
        """Build a deterministic BingX-safe clientOrderId (prefix 'qe-', <= 36 chars)."""
        sym_clean = re.sub(r"[^A-Za-z0-9]", "", symbol).lower()[:8] or "sym"
        act_code = _ACTION_SHORT.get(
            action.upper(),
            re.sub(r"[^A-Za-z0-9]", "", action).lower()[:3] or "act",
        )
        digest = hashlib.sha256(
            f"{decision_id}|{symbol.upper()}|{action.upper()}".encode("utf-8")
        ).hexdigest()[:16]
        coid = f"qe-{sym_clean}-{act_code}-{digest}"
        return coid[:36]

    def reserve(
        self,
        client_order_id: str,
        decision_id: str,
        symbol: str,
        action: str,
    ) -> bool:
        """Atomically reserve client_order_id as PENDING. Returns False if already present."""
        now = _utc_now_iso()
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO orders (
                        client_order_id, decision_id, symbol, action,
                        status, exchange_order_id, error, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, 'PENDING', NULL, NULL, ?, ?)
                    """,
                    (client_order_id, decision_id, symbol, action, now, now),
                )
                conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def update_status(
        self,
        client_order_id: str,
        status: str,
        exchange_order_id: str | None = None,
        error: str | None = None,
    ) -> None:
        now = _utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE orders
                SET status = ?,
                    exchange_order_id = COALESCE(?, exchange_order_id),
                    error = ?,
                    updated_at = ?
                WHERE client_order_id = ?
                """,
                (status, exchange_order_id, error, now, client_order_id),
            )
            conn.commit()

    def get(self, client_order_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM orders WHERE client_order_id = ?",
                (client_order_id,),
            ).fetchone()
        return dict(row) if row is not None else None
