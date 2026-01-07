"""Execution policy enums for smart maker execution."""

from __future__ import annotations

from enum import Enum


class OrderPolicy(str, Enum):
    MAKER_FIRST = "maker_first"
    MAKER_ONLY = "maker_only"


class FallbackPolicy(str, Enum):
    NONE = "none"
    AGGRESSIVE_LIMIT = "aggressive_limit"
    MARKET = "market"


class Market(str, Enum):
    SPOT = "spot"
    USDM = "usdm"


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderState(str, Enum):
    INIT = "init"
    PLACING = "placing"
    LIVE = "live"
    REPRICING = "repricing"
    PARTIAL = "partial"
    CANCELING = "canceling"
    DONE = "done"
    FAILED = "failed"
    ABORTED = "aborted"


class ExecutionStatus(str, Enum):
    DONE = "done"
    ABORTED = "aborted"
    FAILED = "failed"
