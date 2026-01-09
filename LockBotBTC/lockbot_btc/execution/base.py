"""Execution interfaces and config models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ExecutionMode(str, Enum):
    DRY_RUN = "DRY_RUN"
    DEMO_TESTNET = "DEMO_TESTNET"
    LIVE_MAINNET = "LIVE_MAINNET"


@dataclass
class ExecutionConfig:
    mode: ExecutionMode = ExecutionMode.DRY_RUN
    auto_submit_on_allow: bool = False
    allow_live_mainnet: bool = False
    symbol_whitelist: list[str] = field(default_factory=lambda: ["BTCUSDT"])
    max_open_orders: int = 5
    ack_timeout_ms: int = 8000
    stale_account_ms: int = 8000
    error_threshold: int = 3
    allow_reduce_only_in_panic: bool = True
    ledger_path: str = "runtime/lockbot_exec_ledger.jsonl"
    api_key_env: str = "BINANCE_DEMO_API_KEY"
    api_secret_env: str = "BINANCE_DEMO_API_SECRET"
    base_url: str = "https://testnet.binancefuture.com"
    recv_window: int = 5000


@dataclass
class ExecutionGate:
    armed: bool = False
    mode: ExecutionMode = ExecutionMode.DRY_RUN
    arm_until_ms: Optional[int] = None
    last_error: Optional[str] = None
    disarm_reason: Optional[str] = None
    error_count: int = 0

    def is_armed(self, now_ms: int) -> bool:
        if not self.armed:
            return False
        if self.arm_until_ms is not None and now_ms > self.arm_until_ms:
            return False
        return True

    def arm(self, mode: ExecutionMode, ttl_s: int, now_ms: int) -> None:
        self.armed = True
        self.mode = mode
        self.arm_until_ms = now_ms + max(int(ttl_s), 1) * 1000
        self.last_error = None
        self.disarm_reason = None
        self.error_count = 0

    def disarm(self, reason: str) -> None:
        self.armed = False
        self.disarm_reason = reason

    def note_error(self, message: str) -> None:
        self.last_error = message
        self.error_count += 1


@dataclass
class SubmitResult:
    ok: bool
    client_order_id: str
    order_id: Optional[str] = None
    status: Optional[str] = None
    retryable: bool = False
    error_code: Optional[str] = None
    error_detail: Optional[str] = None


@dataclass
class CancelResult:
    ok: bool
    client_order_id: Optional[str] = None
    order_id: Optional[str] = None
    status: Optional[str] = None
    retryable: bool = False
    error_code: Optional[str] = None
    error_detail: Optional[str] = None


@dataclass
class CancelAllResult:
    ok: bool
    status: Optional[str] = None
    retryable: bool = False
    error_code: Optional[str] = None
    error_detail: Optional[str] = None
