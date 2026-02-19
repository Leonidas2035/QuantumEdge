"""Stage 9 dashboard state store for strategies, alerts, and performance."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from supervisor.alerts.engine import AlertEngine, AlertResult
from supervisor.dashboard.audit_log import DashboardAuditLogger

StrategyKey = Tuple[str, str]


@dataclass
class PerformanceCounters:
    closed_deals: int = 0
    wins: int = 0
    losses: int = 0
    net_pnl: float = 0.0
    gross_pnl: float = 0.0
    fees: float = 0.0
    traded_volume_quote: float = 0.0

    def record(
        self, net_pnl: float, gross_pnl: float, fees: float, volume_quote: float
    ) -> None:
        self.closed_deals += 1
        self.net_pnl += net_pnl
        self.gross_pnl += gross_pnl
        self.fees += fees
        self.traded_volume_quote += volume_quote
        if net_pnl > 0:
            self.wins += 1
        elif net_pnl < 0:
            self.losses += 1

    def reset(self) -> None:
        self.closed_deals = 0
        self.wins = 0
        self.losses = 0
        self.net_pnl = 0.0
        self.gross_pnl = 0.0
        self.fees = 0.0
        self.traded_volume_quote = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "closed_deals": self.closed_deals,
            "wins": self.wins,
            "losses": self.losses,
            "net_pnl": self.net_pnl,
            "gross_pnl": self.gross_pnl,
            "fees": self.fees,
            "traded_volume_quote": self.traded_volume_quote,
        }


@dataclass
class StrategyState:
    strategy_id: str
    symbol: str
    telemetry: Dict[str, Any] = field(default_factory=dict)
    telemetry_ts_ms: Optional[int] = None
    limits: Dict[str, Any] = field(default_factory=dict)
    limits_ts_ms: Optional[int] = None
    regime: Dict[str, Any] = field(default_factory=dict)
    regime_ts_ms: Optional[int] = None
    dca_flash: Optional[Dict[str, Any]] = None
    dca_flash_ts_ms: Optional[int] = None
    lot_status: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def last_update_ts_ms(self) -> Optional[int]:
        values = [
            self.telemetry_ts_ms,
            self.limits_ts_ms,
            self.regime_ts_ms,
            self.dca_flash_ts_ms,
        ]
        values = [v for v in values if isinstance(v, int)]
        return max(values) if values else None


class DashboardStateStore:
    def __init__(
        self,
        *,
        audit_logger: DashboardAuditLogger,
        alert_engine: AlertEngine,
        telemetry_stale_ms: int = 5_000,
        cancel_window_sec: int = 60,
        cancel_storm_threshold: int = 20,
        dca_stuck_sell_ms: int = 60_000,
        alert_eval_interval_sec: int = 5,
    ) -> None:
        self._audit = audit_logger
        self._alert_engine = alert_engine
        self._telemetry_stale_ms = int(telemetry_stale_ms)
        self._cancel_window_sec = int(cancel_window_sec)
        self._cancel_storm_threshold = int(cancel_storm_threshold)
        self._dca_stuck_sell_ms = int(dca_stuck_sell_ms)
        self._alert_eval_interval_sec = float(alert_eval_interval_sec)

        self._lock = threading.Lock()
        self._strategies: Dict[StrategyKey, StrategyState] = {}
        self._performance: Dict[StrategyKey, PerformanceCounters] = {}
        self._seen_deals: set[str] = set()
        self._cancel_events: Dict[StrategyKey, list[Tuple[int, int]]] = {}
        self._last_alert_result: Optional[AlertResult] = None
        self._last_alert_eval_ts = 0.0

    def ingest_event(self, payload: Dict[str, Any]) -> None:
        event_type = str(payload.get("type") or payload.get("event_type") or "")
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        ts_ms = _to_ts_ms(payload.get("ts") or payload.get("ts_ms"))
        strategy_id = _coerce_id(
            data.get("strategy_id")
            or payload.get("strategy_id")
            or payload.get("source")
        )
        symbol = _coerce_id(data.get("symbol") or payload.get("symbol"))
        if not strategy_id:
            strategy_id = "unknown"
        if not symbol:
            symbol = "unknown"

        if event_type == "strategy_telemetry.v1":
            self._update_telemetry(strategy_id, symbol, data, ts_ms)
        elif event_type == "strategy_limits.v1":
            self._update_limits(strategy_id, symbol, data, ts_ms)
        elif event_type == "regime_directive.v1":
            self._update_regime(strategy_id, symbol, data, ts_ms)
        elif event_type == "dca_flash_state.v1":
            self._update_dca_flash(strategy_id, symbol, data, ts_ms)
        elif event_type == "dca_lot_status.v1":
            self._update_dca_lot_status(strategy_id, symbol, data, ts_ms)
        elif event_type in {"dca_deal_closed.v1", "scalp_deal_closed.v1"}:
            self._record_deal_closed(strategy_id, symbol, data, ts_ms)

    def evaluate_alerts(self, now_ts: Optional[float] = None) -> AlertResult:
        now = now_ts if now_ts is not None else time.time()
        summary = self.alert_summary(int(now * 1000))
        wrapped = {"dashboard": summary}
        result = self._alert_engine.evaluate(wrapped, now=now)
        self._record_alert_transitions(result, wrapped)
        self._last_alert_result = result
        self._last_alert_eval_ts = now
        return result

    def alerts_snapshot(self) -> Dict[str, Any]:
        now = time.time()
        if now - self._last_alert_eval_ts >= self._alert_eval_interval_sec:
            self.evaluate_alerts(now_ts=now)
        if self._last_alert_result:
            return {
                "active": self._last_alert_result.active,
                "recent": self._last_alert_result.recent,
            }
        return {"active": [], "recent": []}

    def overview(self) -> Dict[str, Any]:
        now_ms = int(time.time() * 1000)
        with self._lock:
            strategies = list(self._strategies.values())
            stale_count = 0
            max_age_ms = 0
            for state in strategies:
                age_ms = _age_ms(state.telemetry_ts_ms, now_ms)
                if age_ms is not None:
                    max_age_ms = max(max_age_ms, age_ms)
                    if age_ms > self._telemetry_stale_ms:
                        stale_count += 1
            perf = self._aggregate_performance()
        alerts = self.alerts_snapshot()
        return {
            "ts_ms": now_ms,
            "strategies_total": len(strategies),
            "strategies_active": len(strategies) - stale_count,
            "alerts_active": len(alerts.get("active", []) or []),
            "stale_telemetry": {"count": stale_count, "max_age_ms": max_age_ms},
            "performance": perf,
        }

    def strategies(self) -> list[Dict[str, Any]]:
        now_ms = int(time.time() * 1000)
        results: list[Dict[str, Any]] = []
        with self._lock:
            for key, state in sorted(self._strategies.items()):
                perf = self._performance.get(key, PerformanceCounters()).to_dict()
                effective_limits = _merge_limits(state.limits, state.regime)
                breaches = _detect_limit_breaches(state.telemetry, state.limits)
                dca_flash = state.dca_flash if state.strategy_id == "DCA_ETH" else None
                dca_lots = _summarize_dca_lots(
                    state.lot_status, now_ms, self._dca_stuck_sell_ms
                )
                results.append(
                    {
                        "strategy_id": state.strategy_id,
                        "symbol": state.symbol,
                        "telemetry": state.telemetry,
                        "limits": state.limits,
                        "regime_directive": state.regime,
                        "effective_limits": effective_limits,
                        "limit_breaches": breaches,
                        "performance": perf,
                        "dca_flash": dca_flash,
                        "dca_lots": dca_lots,
                        "last_update_ts_ms": state.last_update_ts_ms(),
                        "telemetry_age_ms": _age_ms(state.telemetry_ts_ms, now_ms),
                    }
                )
        return results

    def performance(self) -> Dict[str, Any]:
        now_ms = int(time.time() * 1000)
        with self._lock:
            by_strategy = []
            for key, counters in sorted(self._performance.items()):
                strategy_id, symbol = key
                row = {
                    "strategy_id": strategy_id,
                    "symbol": symbol,
                    **counters.to_dict(),
                }
                by_strategy.append(row)
            session = self._aggregate_performance()
        return {"ts_ms": now_ms, "session": session, "by_strategy": by_strategy}

    def audit(self, since_ts_ms: Optional[int], limit: int) -> Dict[str, Any]:
        items = self._audit.read(since_ts_ms=since_ts_ms, limit=limit)
        return {"items": items}

    def reset_counters(self) -> Dict[str, Any]:
        with self._lock:
            for counters in self._performance.values():
                counters.reset()
        self._audit.append(
            severity="INFO",
            component="dashboard",
            strategy_id=None,
            symbol=None,
            event_type="counters_reset",
            payload={"scope": "session"},
        )
        return {"status": "ok", "ts_ms": int(time.time() * 1000)}

    def record_alert_transitions(
        self, result: AlertResult, summary: Dict[str, Any]
    ) -> None:
        self._record_alert_transitions(result, summary)
        self._last_alert_result = result
        self._last_alert_eval_ts = time.time()

    def alert_summary(self, now_ms: Optional[int] = None) -> Dict[str, Any]:
        now_ms = now_ms or int(time.time() * 1000)
        with self._lock:
            stale_count = 0
            max_age_ms = 0
            cancel_count = 0
            cancel_rate = 0
            breaches = 0
            stuck_sells = 0
            api_errors = 0
            for key, state in self._strategies.items():
                age_ms = _age_ms(state.telemetry_ts_ms, now_ms)
                if age_ms is not None:
                    max_age_ms = max(max_age_ms, age_ms)
                    if age_ms > self._telemetry_stale_ms:
                        stale_count += 1
                breaches += len(_detect_limit_breaches(state.telemetry, state.limits))
                api_errors += int(state.telemetry.get("api_errors_1m") or 0)
                stuck_sells += _summarize_dca_lots(
                    state.lot_status, now_ms, self._dca_stuck_sell_ms
                ).get("stuck_sells", 0)
                cancel_count += self._cancel_count_for(key, now_ms)
            cancel_rate = cancel_count
        return {
            "telemetry": {
                "stale_count": stale_count,
                "max_age_ms": max_age_ms,
            },
            "orders": {
                "cancel_count_1m": cancel_count,
                "cancel_rate_1m": cancel_rate,
                "cancel_storm_threshold": self._cancel_storm_threshold,
            },
            "limits": {"breaches": breaches},
            "dca": {"stuck_sells": stuck_sells},
            "health": {"api_errors_1m": api_errors, "degraded": api_errors > 0},
        }

    def _update_telemetry(
        self, strategy_id: str, symbol: str, data: Dict[str, Any], ts_ms: Optional[int]
    ) -> None:
        key = (strategy_id, symbol)
        with self._lock:
            state = self._strategies.get(key) or StrategyState(
                strategy_id=strategy_id, symbol=symbol
            )
            state.telemetry = data
            state.telemetry_ts_ms = ts_ms or int(time.time() * 1000)
            self._strategies[key] = state
            self._ingest_cancel_stats(key, data, state.telemetry_ts_ms)

    def _update_limits(
        self, strategy_id: str, symbol: str, data: Dict[str, Any], ts_ms: Optional[int]
    ) -> None:
        key = (strategy_id, symbol)
        with self._lock:
            state = self._strategies.get(key) or StrategyState(
                strategy_id=strategy_id, symbol=symbol
            )
            before = dict(state.limits)
            state.limits = data
            state.limits_ts_ms = ts_ms or int(time.time() * 1000)
            self._strategies[key] = state
        if before != data:
            self._audit.append(
                severity="INFO",
                component="dashboard",
                strategy_id=strategy_id,
                symbol=symbol,
                event_type="strategy_limits_changed",
                payload={"before": before, "after": data},
            )

    def _update_regime(
        self, strategy_id: str, symbol: str, data: Dict[str, Any], ts_ms: Optional[int]
    ) -> None:
        key = (strategy_id, symbol)
        with self._lock:
            state = self._strategies.get(key) or StrategyState(
                strategy_id=strategy_id, symbol=symbol
            )
            before = dict(state.regime)
            state.regime = data
            state.regime_ts_ms = ts_ms or int(time.time() * 1000)
            self._strategies[key] = state
        if before != data:
            self._audit.append(
                severity="INFO",
                component="dashboard",
                strategy_id=strategy_id,
                symbol=symbol,
                event_type="regime_directive_changed",
                payload={"before": before, "after": data},
            )

    def _update_dca_flash(
        self, strategy_id: str, symbol: str, data: Dict[str, Any], ts_ms: Optional[int]
    ) -> None:
        key = (strategy_id, symbol)
        with self._lock:
            state = self._strategies.get(key) or StrategyState(
                strategy_id=strategy_id, symbol=symbol
            )
            state.dca_flash = data
            state.dca_flash_ts_ms = ts_ms or int(time.time() * 1000)
            self._strategies[key] = state

    def _update_dca_lot_status(
        self, strategy_id: str, symbol: str, data: Dict[str, Any], ts_ms: Optional[int]
    ) -> None:
        lot_id = _coerce_id(data.get("lot_id"))
        if not lot_id:
            return
        key = (strategy_id, symbol)
        with self._lock:
            state = self._strategies.get(key) or StrategyState(
                strategy_id=strategy_id, symbol=symbol
            )
            state.lot_status[lot_id] = {
                "status": data.get("status"),
                "ts_ms": ts_ms or int(time.time() * 1000),
            }
            self._strategies[key] = state

    def _record_deal_closed(
        self, strategy_id: str, symbol: str, data: Dict[str, Any], ts_ms: Optional[int]
    ) -> None:
        deal_id = _coerce_id(
            data.get("deal_id") or data.get("lot_id") or data.get("cycle_id")
        )
        if not deal_id:
            deal_id = f"{strategy_id}:{symbol}:{ts_ms}"
        with self._lock:
            if deal_id in self._seen_deals:
                return
            self._seen_deals.add(deal_id)
            counters = self._performance.get((strategy_id, symbol))
            if not counters:
                counters = PerformanceCounters()
                self._performance[(strategy_id, symbol)] = counters
            net_pnl = _coerce_float(data.get("net_pnl"), default=None)
            gross_pnl = _coerce_float(data.get("gross_pnl"), default=None)
            fees = _coerce_float(data.get("fees"), default=0.0)
            volume_quote = _coerce_float(data.get("volume_quote"), default=0.0)
            if gross_pnl is None:
                gross_pnl = _coerce_float(data.get("pnl"), default=0.0)
            if net_pnl is None:
                net_pnl = gross_pnl - (fees or 0.0) if gross_pnl is not None else 0.0
            counters.record(
                net_pnl or 0.0, gross_pnl or 0.0, fees or 0.0, volume_quote or 0.0
            )
        self._audit.append(
            severity="INFO",
            component="performance",
            strategy_id=strategy_id,
            symbol=symbol,
            event_type="deal_closed",
            correlation_id=deal_id,
            payload=data,
            ts_ms=ts_ms,
        )

    def _aggregate_performance(self) -> Dict[str, Any]:
        totals = PerformanceCounters()
        for counters in self._performance.values():
            totals.closed_deals += counters.closed_deals
            totals.wins += counters.wins
            totals.losses += counters.losses
            totals.net_pnl += counters.net_pnl
            totals.gross_pnl += counters.gross_pnl
            totals.fees += counters.fees
            totals.traded_volume_quote += counters.traded_volume_quote
        return totals.to_dict()

    def _ingest_cancel_stats(
        self, key: StrategyKey, data: Dict[str, Any], ts_ms: int
    ) -> None:
        cancel_count = _coerce_int(data.get("cancel_count_1m"))
        if cancel_count is None:
            cancel_count = _coerce_int(data.get("cancel_count"))
        if cancel_count is None or cancel_count <= 0:
            return
        events = self._cancel_events.get(key) or []
        events.append((ts_ms, cancel_count))
        self._cancel_events[key] = events

    def _cancel_count_for(self, key: StrategyKey, now_ms: int) -> int:
        events = self._cancel_events.get(key) or []
        if not events:
            return 0
        window_ms = self._cancel_window_sec * 1000
        fresh = [(ts, count) for ts, count in events if now_ms - ts <= window_ms]
        self._cancel_events[key] = fresh
        return sum(count for _, count in fresh)

    def _record_alert_transitions(
        self, result: AlertResult, summary: Dict[str, Any]
    ) -> None:
        active = {
            item.get("alert_id") for item in result.active if isinstance(item, dict)
        }
        previous = set()
        if self._last_alert_result:
            previous = {
                item.get("alert_id")
                for item in self._last_alert_result.active
                if isinstance(item, dict)
            }
        for item in result.active:
            if not isinstance(item, dict):
                continue
            if item.get("alert_id") in previous:
                continue
            self._audit.append(
                severity=item.get("severity", "WARN"),
                component="alerts",
                strategy_id=None,
                symbol=None,
                event_type="alert_active",
                correlation_id=str(item.get("alert_id")),
                payload={"alert": item, "summary": summary},
            )
        for item in previous - active:
            self._audit.append(
                severity="INFO",
                component="alerts",
                strategy_id=None,
                symbol=None,
                event_type="alert_resolved",
                correlation_id=str(item),
                payload={"alert_id": item},
            )


def _merge_limits(limits: Dict[str, Any], regime: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(limits or {})
    if not regime:
        return merged
    for key in ("allow_entries", "mode", "risk_state", "throttle"):
        if key in regime:
            merged[key] = regime.get(key)
    return merged


def _detect_limit_breaches(
    telemetry: Dict[str, Any], limits: Dict[str, Any]
) -> list[str]:
    breaches: list[str] = []
    if not telemetry or not limits:
        return breaches
    _check_breach(
        breaches, telemetry, limits, "position_notional", "max_position_notional"
    )
    _check_breach(breaches, telemetry, limits, "inventory_qty", "max_inventory_qty")
    _check_breach(breaches, telemetry, limits, "position_qty", "max_position_qty")
    return breaches


def _check_breach(
    breaches: list[str],
    telemetry: Dict[str, Any],
    limits: Dict[str, Any],
    field: str,
    limit_field: str,
) -> None:
    value = _coerce_float(telemetry.get(field), default=None)
    limit = _coerce_float(limits.get(limit_field), default=None)
    if value is None or limit is None:
        return
    if value > limit:
        breaches.append(field)


def _summarize_dca_lots(
    lots: Dict[str, Dict[str, Any]], now_ms: int, stuck_ms: int
) -> Dict[str, Any]:
    open_lots = 0
    stuck = 0
    for data in lots.values():
        status = str(data.get("status") or "")
        if status.upper() in {"OPEN", "BUY_PENDING", "SELL_PENDING"}:
            open_lots += 1
        if status.upper() == "SELL_PENDING":
            age = _age_ms(data.get("ts_ms"), now_ms)
            if age is not None and age > stuck_ms:
                stuck += 1
    return {"open_lots": open_lots, "stuck_sells": stuck}


def _to_ts_ms(value: object) -> Optional[int]:
    if value is None:
        return None
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return None
    if ts > 1e12:
        return int(ts)
    return int(ts * 1000)


def _age_ms(ts_ms: Optional[int], now_ms: int) -> Optional[int]:
    if ts_ms is None:
        return None
    return max(0, now_ms - int(ts_ms))


def _coerce_id(value: object) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _coerce_float(value: object, default: Optional[float] = None) -> Optional[float]:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_int(value: object) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
