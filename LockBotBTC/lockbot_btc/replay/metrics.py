"""Metrics collector for replay/backtest runs."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class ReplayFillConfig:
    tier: str = "tierA"
    fee_bps: float = 7.0
    slippage_bps: float = 5.0


@dataclass
class ReplayMetrics:
    min_distance_to_liq_bps: Optional[float] = None
    max_margin_usage: Optional[float] = None
    panic_count: int = 0
    ddn_reject_count: int = 0
    stale_data_events: int = 0
    ddn_reject_reasons: Dict[str, int] = field(default_factory=dict)
    cmd_counts: Dict[str, int] = field(default_factory=dict)
    exec_step_count: int = 0
    avg_step_notional: Optional[float] = None
    step_notionals: list[float] = field(default_factory=list)
    rate_limit_hits: int = 0
    paper_pnl_est: float = 0.0
    fees_est: float = 0.0
    slippage_est: float = 0.0
    funding_est: float = 0.0
    net_pnl_est: Optional[float] = None


class MetricsCollector:
    def __init__(self, fill_cfg: ReplayFillConfig) -> None:
        self._fill_cfg = fill_cfg
        self._cmd_counts = Counter()
        self._ddn_reject_reasons = Counter()
        self._ddn_reject_count = 0
        self._last_ddn_verdict: Optional[str] = None
        self._last_mark: Optional[float] = None
        self._paper_pnl = 0.0
        self._sim_delta = 0.0
        self._last_net_delta: Optional[float] = None
        self._step_notionals: list[float] = []
        self._min_liq: Optional[float] = None
        self._max_margin: Optional[float] = None
        self._panic_count = 0
        self._stale_count = 0
        self._rate_limit_hits = 0
        self._fees = 0.0
        self._slippage = 0.0

    def on_policy_decision(self, record: Dict[str, Any]) -> None:
        if record.get("stale"):
            self._stale_count += 1
        reasons = record.get("signals", {}).get("chaos_reasons") or []
        if record.get("reason") == "chaos" or reasons:
            self._stale_count += 0

    def on_command(self, command: Dict[str, Any], mark_price: Optional[float]) -> None:
        payload = command.get("payload") if isinstance(command.get("payload"), dict) else {}
        cmd = payload.get("cmd") or command.get("cmd")
        if cmd:
            self._cmd_counts[str(cmd)] += 1
        if cmd == "PANIC_LOCK":
            self._panic_count += 1
        if cmd == "EXEC_STEP":
            self._cmd_counts["EXEC_STEP"] += 0
            action = payload.get("action")
            qty = payload.get("qty_hint")
            if qty and mark_price:
                notional = float(qty) * float(mark_price)
                self._step_notionals.append(notional)
            if self._fill_cfg.tier.lower() == "tierb" and qty:
                self._apply_fill_model(action, float(qty), mark_price)

    def on_status(self, status: Dict[str, Any]) -> None:
        payload = status.get("payload") if isinstance(status.get("payload"), dict) else {}
        if "payload" in payload:
            payload = payload.get("payload") if isinstance(payload.get("payload"), dict) else payload
        risk = payload.get("risk") if isinstance(payload.get("risk"), dict) else {}
        distance = risk.get("distance_to_liq_bps")
        margin = risk.get("margin_usage")
        if distance is not None:
            distance = float(distance)
            if self._min_liq is None or distance < self._min_liq:
                self._min_liq = distance
        if margin is not None:
            margin = float(margin)
            if self._max_margin is None or margin > self._max_margin:
                self._max_margin = margin
        ddn = payload.get("ddn") if isinstance(payload.get("ddn"), dict) else {}
        verdict = ddn.get("last_verdict")
        if verdict == "PANIC_ONLY":
            self._panic_count += 1
        if verdict == "REJECT" and self._last_ddn_verdict != "REJECT":
            self._ddn_reject_reasons.update(ddn.get("last_reasons") or [])
            self._ddn_reject_count += 1
        if verdict == "REJECT" and "RATE_LIMIT" in (ddn.get("last_reasons") or []):
            self._rate_limit_hits += 1
        self._last_ddn_verdict = verdict
        market = payload.get("market") if isinstance(payload.get("market"), dict) else {}
        mark = market.get("mark_price")
        if mark is not None:
            mark = float(mark)
            delta = payload.get("net_delta_est")
            if self._fill_cfg.tier.lower() == "tiera":
                if delta is not None and self._last_mark is not None:
                    self._paper_pnl += float(delta) * (mark - self._last_mark)
            else:
                if self._last_mark is not None:
                    self._paper_pnl += self._sim_delta * (mark - self._last_mark)
            self._last_mark = mark
            if delta is not None:
                self._last_net_delta = float(delta)

    def build(self) -> ReplayMetrics:
        avg_notional = None
        if self._step_notionals:
            avg_notional = sum(self._step_notionals) / len(self._step_notionals)
        metrics = ReplayMetrics(
            min_distance_to_liq_bps=self._min_liq,
            max_margin_usage=self._max_margin,
            panic_count=self._panic_count,
            ddn_reject_count=self._ddn_reject_count,
            stale_data_events=self._stale_count,
            ddn_reject_reasons=dict(self._ddn_reject_reasons),
            cmd_counts=dict(self._cmd_counts),
            exec_step_count=self._cmd_counts.get("EXEC_STEP", 0),
            avg_step_notional=avg_notional,
            step_notionals=list(self._step_notionals),
            rate_limit_hits=self._rate_limit_hits,
            paper_pnl_est=self._paper_pnl,
            fees_est=self._fees,
            slippage_est=self._slippage,
            funding_est=0.0,
            net_pnl_est=self._paper_pnl - self._fees - self._slippage,
        )
        return metrics

    def _apply_fill_model(self, action: str, qty: float, mark_price: Optional[float]) -> None:
        if mark_price is None:
            return
        if action == "ADD_LONG":
            self._sim_delta += qty
        elif action == "ADD_SHORT":
            self._sim_delta -= qty
        elif action == "TRIM_LONG":
            self._sim_delta -= qty
        elif action == "TRIM_SHORT":
            self._sim_delta += qty
        fee = qty * mark_price * (self._fill_cfg.fee_bps / 10000.0)
        slip = qty * mark_price * (self._fill_cfg.slippage_bps / 10000.0)
        self._fees += fee
        self._slippage += slip
