"""LockBotBTC policy models and configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import yaml


@dataclass
class OhlcvBar:
    ts_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class LiqHeatmapSummary:
    intensity_above: float = 0.0
    intensity_below: float = 0.0
    last_ts: Optional[int] = None


@dataclass
class MarketSnapshot:
    symbol: str
    mark_price: Optional[float] = None
    mark_ts: Optional[int] = None
    vwap: Optional[float] = None
    band_1u: Optional[float] = None
    band_1l: Optional[float] = None
    band_2u: Optional[float] = None
    band_2l: Optional[float] = None
    avwap: Optional[float] = None
    avwap_anchor: Optional[str] = None
    avwap_anchors: Dict[str, float] = field(default_factory=dict)
    funding_rate: Optional[float] = None
    funding_ts: Optional[int] = None
    liq: LiqHeatmapSummary = field(default_factory=LiqHeatmapSummary)
    ohlcv_5m: List[OhlcvBar] = field(default_factory=list)
    ohlcv_15m: List[OhlcvBar] = field(default_factory=list)


@dataclass
class BotStatusSnapshot:
    mode: str
    regime: str
    net_delta: float
    long_qty: float
    short_qty: float
    market_lag_ms: Optional[int]
    account_lag_ms: Optional[int]
    ddn_verdict: Optional[str]
    ddn_reasons: Sequence[str]
    last_cmd_type: Optional[str]
    last_cmd_id: Optional[str]
    last_cmd_ts: Optional[int]


@dataclass
class PolicyIntent:
    cmd: str
    payload: Dict[str, Any]
    reason: str
    priority: int = 100


@dataclass
class StrategyDecision:
    intent: Optional[PolicyIntent]
    action: str
    reason: str
    debug: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RegimeSignals:
    adx: Optional[float]
    atr: Optional[float]
    atr_baseline: Optional[float]
    slope_bps: Optional[float]
    chaos: bool
    chaos_reasons: List[str] = field(default_factory=list)


@dataclass
class RegimeDetectorConfig:
    adx_period: int = 14
    atr_period: int = 14
    atr_baseline_period: int = 50
    ema_fast: int = 8
    ema_slow: int = 21
    trend_adx_enter: float = 25.0
    trend_adx_exit: float = 20.0
    slope_bps_enter: float = 6.0
    chaos_atr_mult: float = 2.0
    chaos_liq_intensity: float = 5.0
    chaos_band_bps: float = 120.0
    min_regime_hold_s: int = 60
    confirm_cycles: int = 2


@dataclass
class RangePolicyConfig:
    target: float = 0.0
    band_low: float = -0.08
    band_high: float = 0.08
    step_qty_hint: float = 0.01
    heatmap_block: float = 4.0
    funding_max_abs: float = 0.0005
    min_edge_bps: float = 2.0


@dataclass
class TrendPolicyConfig:
    target_up: float = 0.35
    target_down: float = -0.35
    band_low: float = -0.6
    band_high: float = 0.6
    step_qty_hint: float = 0.02
    pullback_bps: float = 35.0
    heatmap_block: float = 6.0
    funding_max_abs: float = 0.0008
    min_edge_bps: float = 2.5
    target_refresh_s: int = 30
    avwap_anchor_preference: Sequence[str] = ("trend_start", "lock_entry", "liq_sweep")


@dataclass
class PolicyRunnerConfig:
    enabled: bool = False
    symbol: str = "BTCUSDT"
    hub_sub_endpoint: str = "ipc:///tmp/quantum_market_data.ipc"
    hub_topics: List[str] = field(
        default_factory=lambda: [
            "BTCUSDT:mark_price_1s",
            "BTCUSDT:ohlcv_5m",
            "BTCUSDT:ohlcv_15m",
            "BTCUSDT:vwap_bands_d",
            "BTCUSDT:avwap",
            "BTCUSDT:liq_heatmap",
            "BTCUSDT:funding_rate",
        ]
    )
    tick_interval_ms: int = 1000
    max_market_lag_ms: int = 3000
    max_account_lag_ms: int = 8000
    stale_action: str = "PAUSE"
    max_cmds_per_sec: int = 2
    max_exec_steps_per_minute: int = 6
    max_cmds_per_tick: int = 1
    cooldown_after_reject_ms: int = 2000
    min_leg_qty: float = 0.001
    execution_enabled: bool = False
    reject_pause_threshold: int = 5
    audit_log_path: str = "runtime/lockbot_policy_decisions.jsonl"
    regime: RegimeDetectorConfig = field(default_factory=RegimeDetectorConfig)
    range_policy: RangePolicyConfig = field(default_factory=RangePolicyConfig)
    trend_policy: TrendPolicyConfig = field(default_factory=TrendPolicyConfig)


def _coerce_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def load_lockbot_policy_config(path: Path) -> PolicyRunnerConfig:
    if not path.exists():
        return PolicyRunnerConfig(enabled=False)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg = PolicyRunnerConfig(enabled=bool(raw.get("enabled", True)))
    cfg.symbol = str(raw.get("symbol", cfg.symbol))
    cfg.hub_sub_endpoint = str(raw.get("hub_sub_endpoint", cfg.hub_sub_endpoint))
    topics = raw.get("hub_topics")
    if isinstance(topics, list) and topics:
        cfg.hub_topics = [str(t) for t in topics]
    cfg.tick_interval_ms = _coerce_int(raw.get("tick_interval_ms"), cfg.tick_interval_ms)
    cfg.max_market_lag_ms = _coerce_int(raw.get("max_market_lag_ms"), cfg.max_market_lag_ms)
    cfg.max_account_lag_ms = _coerce_int(raw.get("max_account_lag_ms"), cfg.max_account_lag_ms)
    cfg.stale_action = str(raw.get("stale_action", cfg.stale_action)).upper()
    cfg.max_cmds_per_sec = _coerce_int(raw.get("max_cmds_per_sec"), cfg.max_cmds_per_sec)
    cfg.max_exec_steps_per_minute = _coerce_int(raw.get("max_exec_steps_per_minute"), cfg.max_exec_steps_per_minute)
    cfg.max_cmds_per_tick = _coerce_int(raw.get("max_cmds_per_tick"), cfg.max_cmds_per_tick)
    cfg.cooldown_after_reject_ms = _coerce_int(raw.get("cooldown_after_reject_ms"), cfg.cooldown_after_reject_ms)
    cfg.min_leg_qty = _coerce_float(raw.get("min_leg_qty"), cfg.min_leg_qty)
    cfg.execution_enabled = bool(raw.get("execution_enabled", cfg.execution_enabled))
    cfg.reject_pause_threshold = _coerce_int(raw.get("reject_pause_threshold"), cfg.reject_pause_threshold)
    cfg.audit_log_path = str(raw.get("audit_log_path", cfg.audit_log_path))

    regime_raw = raw.get("regime", {}) if isinstance(raw, dict) else {}
    cfg.regime = RegimeDetectorConfig(
        adx_period=_coerce_int(regime_raw.get("adx_period"), cfg.regime.adx_period),
        atr_period=_coerce_int(regime_raw.get("atr_period"), cfg.regime.atr_period),
        atr_baseline_period=_coerce_int(regime_raw.get("atr_baseline_period"), cfg.regime.atr_baseline_period),
        ema_fast=_coerce_int(regime_raw.get("ema_fast"), cfg.regime.ema_fast),
        ema_slow=_coerce_int(regime_raw.get("ema_slow"), cfg.regime.ema_slow),
        trend_adx_enter=_coerce_float(regime_raw.get("trend_adx_enter"), cfg.regime.trend_adx_enter),
        trend_adx_exit=_coerce_float(regime_raw.get("trend_adx_exit"), cfg.regime.trend_adx_exit),
        slope_bps_enter=_coerce_float(regime_raw.get("slope_bps_enter"), cfg.regime.slope_bps_enter),
        chaos_atr_mult=_coerce_float(regime_raw.get("chaos_atr_mult"), cfg.regime.chaos_atr_mult),
        chaos_liq_intensity=_coerce_float(regime_raw.get("chaos_liq_intensity"), cfg.regime.chaos_liq_intensity),
        chaos_band_bps=_coerce_float(regime_raw.get("chaos_band_bps"), cfg.regime.chaos_band_bps),
        min_regime_hold_s=_coerce_int(regime_raw.get("min_regime_hold_s"), cfg.regime.min_regime_hold_s),
        confirm_cycles=_coerce_int(regime_raw.get("confirm_cycles"), cfg.regime.confirm_cycles),
    )

    range_raw = raw.get("range", {}) if isinstance(raw, dict) else {}
    cfg.range_policy = RangePolicyConfig(
        target=_coerce_float(range_raw.get("target"), cfg.range_policy.target),
        band_low=_coerce_float(range_raw.get("band_low"), cfg.range_policy.band_low),
        band_high=_coerce_float(range_raw.get("band_high"), cfg.range_policy.band_high),
        step_qty_hint=_coerce_float(range_raw.get("step_qty_hint"), cfg.range_policy.step_qty_hint),
        heatmap_block=_coerce_float(range_raw.get("heatmap_block"), cfg.range_policy.heatmap_block),
        funding_max_abs=_coerce_float(range_raw.get("funding_max_abs"), cfg.range_policy.funding_max_abs),
        min_edge_bps=_coerce_float(range_raw.get("min_edge_bps"), cfg.range_policy.min_edge_bps),
    )

    trend_raw = raw.get("trend", {}) if isinstance(raw, dict) else {}
    avwap_pref = trend_raw.get("avwap_anchor_preference")
    if isinstance(avwap_pref, list) and avwap_pref:
        avwap_pref = tuple(str(val) for val in avwap_pref)
    else:
        avwap_pref = cfg.trend_policy.avwap_anchor_preference
    cfg.trend_policy = TrendPolicyConfig(
        target_up=_coerce_float(trend_raw.get("target_up"), cfg.trend_policy.target_up),
        target_down=_coerce_float(trend_raw.get("target_down"), cfg.trend_policy.target_down),
        band_low=_coerce_float(trend_raw.get("band_low"), cfg.trend_policy.band_low),
        band_high=_coerce_float(trend_raw.get("band_high"), cfg.trend_policy.band_high),
        step_qty_hint=_coerce_float(trend_raw.get("step_qty_hint"), cfg.trend_policy.step_qty_hint),
        pullback_bps=_coerce_float(trend_raw.get("pullback_bps"), cfg.trend_policy.pullback_bps),
        heatmap_block=_coerce_float(trend_raw.get("heatmap_block"), cfg.trend_policy.heatmap_block),
        funding_max_abs=_coerce_float(trend_raw.get("funding_max_abs"), cfg.trend_policy.funding_max_abs),
        min_edge_bps=_coerce_float(trend_raw.get("min_edge_bps"), cfg.trend_policy.min_edge_bps),
        target_refresh_s=_coerce_int(trend_raw.get("target_refresh_s"), cfg.trend_policy.target_refresh_s),
        avwap_anchor_preference=avwap_pref,
    )
    return cfg
