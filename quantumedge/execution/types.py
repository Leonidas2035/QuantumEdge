"""Typed execution contracts for smart maker executor."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Optional, Protocol

from quantumedge.execution.policies import FallbackPolicy, Market, OrderPolicy, OrderSide


@dataclass(frozen=True)
class OrderRequest:
    symbol: str
    side: OrderSide
    quantity: Decimal
    signal_price: Decimal
    market: Market
    client_order_id: Optional[str] = None
    reduce_only: bool = False


@dataclass(frozen=True)
class BookState:
    symbol: str
    best_bid_px: Decimal
    best_bid_qty: Decimal
    best_ask_px: Decimal
    best_ask_qty: Decimal
    ts_ms: int
    tick_size: Optional[Decimal] = None


@dataclass(frozen=True)
class OrderPlacement:
    symbol: str
    side: OrderSide
    quantity: Decimal
    price: Optional[Decimal]
    order_type: str
    time_in_force: Optional[str]
    post_only: bool
    client_order_id: Optional[str]
    reduce_only: bool


@dataclass(frozen=True)
class OrderAck:
    order_id: Optional[str]
    client_order_id: Optional[str]
    status: Optional[str] = None
    filled_qty: Optional[Decimal] = None
    avg_price: Optional[Decimal] = None


@dataclass(frozen=True)
class ExecutionReport:
    order_id: Optional[str]
    client_order_id: Optional[str]
    status: str
    filled_qty: Decimal
    avg_price: Optional[Decimal] = None
    fee: Optional[Decimal] = None
    ts_ms: Optional[int] = None


@dataclass(frozen=True)
class ExecutionResult:
    status: str
    reason: str
    filled_qty: Decimal
    remaining_qty: Decimal
    avg_fill_price: Optional[Decimal]
    fees: Optional[Decimal]
    order_id: Optional[str]
    client_order_id: Optional[str]
    lifetime_ms: int
    time_to_fill_ms: Optional[int]
    reprices: int
    chase_count: int
    effective_spread_bps: Optional[Decimal]


@dataclass(frozen=True)
class SmartMakerConfig:
    order_policy: OrderPolicy = OrderPolicy.MAKER_FIRST
    fallback_policy: FallbackPolicy = FallbackPolicy.AGGRESSIVE_LIMIT
    max_slippage_bps: Optional[Decimal] = None
    max_slippage_abs: Optional[Decimal] = None
    reprice_ticks: int = 1
    max_reprices: int = 3
    max_lifetime_ms: int = 5000
    min_reprice_interval_ms: int = 200
    maker_timeout_ms: int = 1200
    aggressive_limit_offset_ticks: int = 1
    aggressive_limit_ttl_ms: int = 300
    spread_max_bps: Optional[Decimal] = None
    min_top_depth_qty: Optional[Decimal] = None
    min_top_depth_usd: Optional[Decimal] = None
    min_remaining_qty: Optional[Decimal] = None
    min_notional: Optional[Decimal] = None
    enable_cancel_replace_throttle: bool = True
    throttle_ms: int = 250
    poll_interval_ms: int = 50

    @staticmethod
    def _as_decimal(value: object) -> Optional[Decimal]:
        if value is None:
            return None
        if isinstance(value, Decimal):
            return value
        try:
            return Decimal(str(value))
        except Exception:
            return None

    @classmethod
    def from_dict(cls, raw: dict) -> "SmartMakerConfig":
        data = raw or {}
        try:
            order_policy = OrderPolicy(str(data.get("order_policy", OrderPolicy.MAKER_FIRST)).lower())
        except ValueError:
            order_policy = OrderPolicy.MAKER_FIRST
        try:
            fallback_policy = FallbackPolicy(str(data.get("fallback_policy", FallbackPolicy.AGGRESSIVE_LIMIT)).lower())
        except ValueError:
            fallback_policy = FallbackPolicy.AGGRESSIVE_LIMIT
        return cls(
            order_policy=order_policy,
            fallback_policy=fallback_policy,
            max_slippage_bps=cls._as_decimal(data.get("max_slippage_bps")),
            max_slippage_abs=cls._as_decimal(data.get("max_slippage_abs")),
            reprice_ticks=int(data.get("reprice_ticks", cls.reprice_ticks)),
            max_reprices=int(data.get("max_reprices", cls.max_reprices)),
            max_lifetime_ms=int(data.get("max_lifetime_ms", cls.max_lifetime_ms)),
            min_reprice_interval_ms=int(data.get("min_reprice_interval_ms", cls.min_reprice_interval_ms)),
            maker_timeout_ms=int(data.get("maker_timeout_ms", cls.maker_timeout_ms)),
            aggressive_limit_offset_ticks=int(data.get("aggressive_limit_offset_ticks", cls.aggressive_limit_offset_ticks)),
            aggressive_limit_ttl_ms=int(data.get("aggressive_limit_ttl_ms", cls.aggressive_limit_ttl_ms)),
            spread_max_bps=cls._as_decimal(data.get("spread_max_bps")),
            min_top_depth_qty=cls._as_decimal(data.get("min_top_depth_qty")),
            min_top_depth_usd=cls._as_decimal(data.get("min_top_depth_usd")),
            min_remaining_qty=cls._as_decimal(data.get("min_remaining_qty")),
            min_notional=cls._as_decimal(data.get("min_notional")),
            enable_cancel_replace_throttle=bool(data.get("enable_cancel_replace_throttle", cls.enable_cancel_replace_throttle)),
            throttle_ms=int(data.get("throttle_ms", cls.throttle_ms)),
            poll_interval_ms=int(data.get("poll_interval_ms", cls.poll_interval_ms)),
        )


class ExecutionClient(Protocol):
    async def place_order(self, placement: OrderPlacement) -> OrderAck:
        ...

    async def cancel_order(
        self,
        *,
        symbol: str,
        order_id: Optional[str] = None,
        client_order_id: Optional[str] = None,
    ) -> bool:
        ...


BookProvider = Callable[[], Optional[BookState]]
