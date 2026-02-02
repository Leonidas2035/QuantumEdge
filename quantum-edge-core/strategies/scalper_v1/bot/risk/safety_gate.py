"""Centralized safety gate for trade intents."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Dict, Optional


@dataclass
class SafetyDecision:
    allow: bool
    reason: str
    details: Dict[str, Any]


class DataFreshnessMonitor:
    def __init__(self, max_tick_ms: int = 0, max_book_ms: int = 0) -> None:
        self.max_tick_ms = max(int(max_tick_ms), 0)
        self.max_book_ms = max(int(max_book_ms), 0)
        self.last_tick_ms: Optional[int] = None
        self.last_book_ms: Optional[int] = None

    def update_tick(self, ts_ms: int) -> None:
        self.last_tick_ms = int(ts_ms)

    def update_book(self, ts_ms: int) -> None:
        self.last_book_ms = int(ts_ms)

    def snapshot(self, now_ms: Optional[int] = None) -> Dict[str, Optional[int]]:
        now_ms = int(now_ms or time.time() * 1000)
        tick_age = None if self.last_tick_ms is None else max(0, now_ms - self.last_tick_ms)
        book_age = None if self.last_book_ms is None else max(0, now_ms - self.last_book_ms)
        return {"tick_age_ms": tick_age, "book_age_ms": book_age}

    def is_stale(self, now_ms: Optional[int] = None) -> bool:
        now_ms = int(now_ms or time.time() * 1000)
        if self.max_tick_ms and self.last_tick_ms is not None:
            if now_ms - self.last_tick_ms > self.max_tick_ms:
                return True
        if self.max_book_ms and self.last_book_ms is not None:
            if now_ms - self.last_book_ms > self.max_book_ms:
                return True
        return False


class SafetyGate:
    """Evaluates risk limits, data freshness, and breaker status before trading."""

    def __init__(self, cfg: Optional[Dict[str, Any]] = None) -> None:
        cfg = cfg or {}
        self.max_position_notional = float(cfg.get("max_position_notional", 0.0) or 0.0)
        self.max_position_pct_equity = float(cfg.get("max_position_pct_equity", 0.0) or 0.0)
        self.max_leverage = float(cfg.get("max_leverage", 0.0) or 0.0)
        self.max_orders_per_min = int(cfg.get("max_orders_per_min", 0) or 0)
        self.max_trades_per_hour = int(cfg.get("max_trades_per_hour", 0) or 0)
        self.max_daily_loss_pct = float(cfg.get("max_daily_loss_pct", 0.0) or 0.0)
        self.max_drawdown_pct = float(cfg.get("max_drawdown_pct", 0.0) or 0.0)
        self.max_slippage_bps = float(cfg.get("max_slippage_bps", 0.0) or 0.0)
        self.max_spread_bps = float(cfg.get("max_spread_bps", 0.0) or 0.0)
        self.min_depth = float(cfg.get("min_depth", 0.0) or 0.0)
        self.allow_exit_on_block = bool(cfg.get("allow_exit_on_block", True))

    def evaluate(
        self,
        intent: Dict[str, Any],
        *,
        breaker_reason: Optional[str] = None,
        data_stale: bool = False,
        kill_switch: bool = False,
    ) -> SafetyDecision:
        action = str(intent.get("action") or "")
        reduce_only = bool(intent.get("reduce_only", False))
        is_exit = reduce_only or action == "close"
        details: Dict[str, Any] = {}

        if kill_switch and not (is_exit and self.allow_exit_on_block):
            return SafetyDecision(False, "KILL_SWITCH_ACTIVE", details)
        if breaker_reason and not (is_exit and self.allow_exit_on_block):
            return SafetyDecision(False, f"CIRCUIT_BREAKER_ACTIVE:{breaker_reason}", details)
        if data_stale and not (is_exit and self.allow_exit_on_block):
            return SafetyDecision(False, "DATA_STALE", details)

        if is_exit:
            return SafetyDecision(True, "OK", details)

        notional = float(intent.get("notional", 0.0) or 0.0)
        equity = intent.get("equity")
        position_notional = float(intent.get("position_notional", 0.0) or 0.0)
        orders_last_min = int(intent.get("orders_last_min", 0) or 0)
        trades_last_hour = int(intent.get("trades_last_hour", 0) or 0)
        daily_loss_pct = intent.get("daily_loss_pct")
        drawdown_pct = intent.get("drawdown_pct")
        spread_bps = intent.get("spread_bps")
        depth_usd = intent.get("depth_usd")

        if self.max_orders_per_min and orders_last_min >= self.max_orders_per_min:
            return SafetyDecision(False, "RATE_LIMIT_ORDERS", {"orders_last_min": orders_last_min})
        if self.max_trades_per_hour and trades_last_hour >= self.max_trades_per_hour:
            return SafetyDecision(False, "RATE_LIMIT_TRADES", {"trades_last_hour": trades_last_hour})
        if self.max_position_notional and (position_notional + notional) > self.max_position_notional:
            return SafetyDecision(False, "RISK_LIMIT_NOTIONAL", {"position_notional": position_notional, "notional": notional})
        if equity and self.max_position_pct_equity:
            pct = (position_notional + notional) / float(equity)
            if pct > self.max_position_pct_equity:
                return SafetyDecision(False, "RISK_LIMIT_EQUITY_PCT", {"position_pct": pct})
        if equity and self.max_leverage:
            lev = (position_notional + notional) / float(equity)
            if lev > self.max_leverage:
                return SafetyDecision(False, "RISK_LIMIT_LEVERAGE", {"leverage": lev})
        if self.max_daily_loss_pct and daily_loss_pct is not None:
            if daily_loss_pct >= self.max_daily_loss_pct:
                return SafetyDecision(False, "RISK_LIMIT_DAILY_LOSS", {"daily_loss_pct": daily_loss_pct})
        if self.max_drawdown_pct and drawdown_pct is not None:
            if drawdown_pct >= self.max_drawdown_pct:
                return SafetyDecision(False, "RISK_LIMIT_DRAWDOWN", {"drawdown_pct": drawdown_pct})
        if self.max_spread_bps and spread_bps is not None and spread_bps > self.max_spread_bps:
            return SafetyDecision(False, "SPREAD_TOO_WIDE", {"spread_bps": spread_bps})
        if self.min_depth and depth_usd is not None and depth_usd < self.min_depth:
            return SafetyDecision(False, "DEPTH_TOO_THIN", {"depth_usd": depth_usd})

        return SafetyDecision(True, "OK", details)
