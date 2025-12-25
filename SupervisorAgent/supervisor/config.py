"""Configuration loading utilities for SupervisorAgent."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, List

import yaml


@dataclass
class PathsConfig:
    """File system layout for SupervisorAgent and external dependencies."""

    quantumedge_root: Path
    python_executable: Path
    meta_agent_root: Path
    logs_dir: Path
    events_dir: Path
    reports_dir: Path


@dataclass
class SupervisorConfig:
    """Runtime configuration values for the supervisor process."""

    mode: str
    heartbeat_port: int
    heartbeat_timeout_s: float
    restart_max_attempts: int
    restart_backoff_s: float
    exchange: str = ""
    api_enabled: bool = True
    api_host: str = "127.0.0.1"
    api_auth_token: str = ""


@dataclass
class RiskConfig:
    """Global risk parameters applied by the RiskEngine."""

    currency: str
    max_daily_loss_abs: float
    max_daily_loss_pct: Optional[float]
    max_drawdown_abs: Optional[float]
    max_drawdown_pct: Optional[float]
    max_notional_per_symbol: float
    max_leverage: float


@dataclass
class LlmSupervisorTrustPolicy:
    """Policy controlling what LLM advice may change."""

    allow_risk_multiplier: bool
    allow_mode_switch: bool
    allow_pause: bool
    min_multiplier: float
    max_multiplier: float


@dataclass
class LlmSupervisorConfig:
    """Configuration for the LLM-based risk moderator."""

    enabled: bool
    api_url: str
    model: str
    api_key_env: str
    check_interval_minutes: int
    min_order_decisions: int
    max_events_in_summary: int
    max_trades_in_table: int
    timeout_seconds: int
    dry_run: bool
    trust_policy: LlmSupervisorTrustPolicy


@dataclass
class MetaSupervisorConfig:
    """Configuration for Meta-Agent orchestration."""

    enabled: bool
    meta_agent_root: Optional[Path]
    python_executable: Optional[Path]
    project_id: str
    frequency_days: int
    min_hours_between_runs: int
    require_bot_idle: bool
    dry_run: bool
    use_supervisor_runner: bool
    task_title_prefix: str
    extra_tags: List[str]
    max_audit_days: int


@dataclass
class TrendEvaluatorConfig:
    """Configuration for the trend evaluator."""

    enabled: bool
    model: str
    temperature: float
    timeout_seconds: float
    history_window_minutes: int
    include_volatility: bool
    include_signal_stats: bool
    max_calls_per_minute: int
    cache_enabled: bool
    cache_ttl_seconds: int


@dataclass
class MarketRiskMonitorConfig:
    """Configuration for the market risk monitor."""

    enabled: bool
    model: str
    temperature: float
    timeout_seconds: float
    history_window_minutes: int
    include_liquidations: bool
    include_orderbook_imbalance: bool
    risk_scale: Dict[str, int]
    max_calls_per_minute: int


@dataclass
class TradingBehaviorConfig:
    """Configuration for the trading behavior analyzer."""

    enabled: bool
    model: str
    temperature: float
    timeout_seconds: float
    history_trades: int
    history_signals: int
    max_calls_per_minute: int


@dataclass
class SnapshotSchedulerConfig:
    """Configuration for periodic supervisor snapshots."""

    enabled: bool
    interval_minutes: int
    history_window_minutes: int


@dataclass
class DashboardConfig:
    """Configuration for dashboard backend."""

    enabled: bool
    max_events: int
    events_types: List[str]
    pnl_window_minutes: int
    max_snapshots: int
    require_snapshot_recent_minutes: int
    require_heartbeat_recent_seconds: int


@dataclass
class TsdbConfig:
    """Configuration for TSDB layer."""

    enabled: bool
    backend: str
    flush_interval_seconds: int
    batch_size: int
    table_prefix: str
    clickhouse_url: str
    clickhouse_database: str
    clickhouse_user: str
    clickhouse_password: str
    questdb_ilp_http_url: str
    retry_max_retries: int
    retry_base_backoff_ms: int
    retry_max_backoff_ms: int
    backfill_enabled: bool
    backfill_from_days: int


@dataclass
class TsdbRetentionConfig:
    """Retention and rollup configuration."""

    enabled: bool
    raw_days: int
    rollup_1m_days: int
    rollup_1h_days: int
    rollups_enabled: bool
    rollup_intervals: List[Dict[str, Any]]


def _load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Configuration file must contain a mapping: {path}")
    return data


def load_paths_config(path: Path) -> PathsConfig:
    """Load filesystem-related configuration from YAML."""

    raw = _load_yaml(path)
    project_root = path.parent.parent.resolve()

    quantumedge_root_value = raw.get("quantumedge_root")
    if not quantumedge_root_value:
        raise ValueError("quantumedge_root must be specified in paths config")
    quantumedge_root = Path(quantumedge_root_value).expanduser()

    python_executable_value = raw.get("python_executable") or sys.executable
    python_executable = Path(python_executable_value).expanduser()

    meta_agent_root_value = raw.get("meta_agent_root", "")
    meta_agent_root = Path(meta_agent_root_value).expanduser() if meta_agent_root_value else project_root

    logs_dir_value = raw.get("logs_dir")
    logs_dir = Path(logs_dir_value).expanduser() if logs_dir_value else project_root / "logs"

    events_dir_value = raw.get("events_dir")
    events_dir = Path(events_dir_value).expanduser() if events_dir_value else logs_dir / "events"

    reports_dir_value = raw.get("reports_dir")
    reports_dir = Path(reports_dir_value).expanduser() if reports_dir_value else project_root / "reports"

    return PathsConfig(
        quantumedge_root=quantumedge_root.resolve(),
        python_executable=python_executable.resolve(),
        meta_agent_root=meta_agent_root.resolve(),
        logs_dir=logs_dir.resolve(),
        events_dir=events_dir.resolve(),
        reports_dir=reports_dir.resolve(),
    )


def load_supervisor_config(path: Path) -> SupervisorConfig:
    """Load supervisor runtime configuration from YAML."""

    raw = _load_yaml(path)
    mode = raw.get("mode", "paper")
    allowed_modes = {"paper", "demo", "off"}
    if mode not in allowed_modes:
        raise ValueError(f"Invalid mode '{mode}'. Allowed: {', '.join(sorted(allowed_modes))}")

    heartbeat_port = int(raw.get("heartbeat_port", 8765))
    heartbeat_timeout_s = float(raw.get("heartbeat_timeout_s", 15))
    restart_max_attempts = int(raw.get("restart_max_attempts", 3))
    restart_backoff_s = float(raw.get("restart_backoff_s", 5))
    if heartbeat_port < 1 or heartbeat_port > 65535:
        raise ValueError("heartbeat_port must be between 1 and 65535")

    return SupervisorConfig(
        mode=mode,
        heartbeat_port=heartbeat_port,
        heartbeat_timeout_s=heartbeat_timeout_s,
        restart_max_attempts=restart_max_attempts,
        restart_backoff_s=restart_backoff_s,
        exchange=str(raw.get("exchange", "")),
        api_enabled=bool(raw.get("api_enabled", True)),
        api_host=str(raw.get("api_host", "127.0.0.1")),
        api_auth_token=str(raw.get("api_auth_token", "")),
    )


def load_risk_config(path: Path) -> RiskConfig:
    """Load global risk configuration."""

    raw = _load_yaml(path)
    currency = raw.get("currency", "USDT")
    max_daily_loss_abs = float(raw.get("max_daily_loss_abs", 0.0))
    max_daily_loss_pct = raw.get("max_daily_loss_pct")
    max_drawdown_abs = raw.get("max_drawdown_abs")
    max_drawdown_pct = raw.get("max_drawdown_pct")
    max_notional_per_symbol = float(raw.get("max_notional_per_symbol", 0.0))
    max_leverage = float(raw.get("max_leverage", 0.0))

    def _validate_positive(val: Optional[float], name: str, allow_zero: bool = False) -> Optional[float]:
        if val is None:
            return None
        val_f = float(val)
        if val_f < 0 or (not allow_zero and val_f == 0):
            raise ValueError(f"{name} must be positive")
        return val_f

    max_daily_loss_abs = _validate_positive(max_daily_loss_abs, "max_daily_loss_abs")
    max_daily_loss_pct = _validate_positive(max_daily_loss_pct, "max_daily_loss_pct", allow_zero=True)
    max_drawdown_abs = _validate_positive(max_drawdown_abs, "max_drawdown_abs", allow_zero=True)
    max_drawdown_pct = _validate_positive(max_drawdown_pct, "max_drawdown_pct", allow_zero=True)
    max_notional_per_symbol = _validate_positive(max_notional_per_symbol, "max_notional_per_symbol")
    max_leverage = _validate_positive(max_leverage, "max_leverage")

    return RiskConfig(
        currency=currency,
        max_daily_loss_abs=max_daily_loss_abs,
        max_daily_loss_pct=max_daily_loss_pct,
        max_drawdown_abs=max_drawdown_abs,
        max_drawdown_pct=max_drawdown_pct,
        max_notional_per_symbol=max_notional_per_symbol,
        max_leverage=max_leverage,
    )


def load_meta_supervisor_config(path: Path, paths: PathsConfig) -> MetaSupervisorConfig:
    """Load Meta-Agent supervisor configuration."""

    def _defaults() -> MetaSupervisorConfig:
        return MetaSupervisorConfig(
            enabled=False,
            meta_agent_root=paths.meta_agent_root,
            python_executable=Path(sys.executable),
            project_id="ai_scalper_bot",
            frequency_days=1,
            min_hours_between_runs=12,
            require_bot_idle=True,
            dry_run=True,
            use_supervisor_runner=True,
            task_title_prefix="S1 ai_scalper_bot",
            extra_tags=["supervisor", "ai_scalper_bot"],
            max_audit_days=1,
        )

    if not path.exists():
        return _defaults()

    raw = _load_yaml(path)
    base = _defaults()

    meta_agent_root_raw = raw.get("meta_agent_root")
    meta_agent_root = Path(meta_agent_root_raw).expanduser() if meta_agent_root_raw else paths.meta_agent_root

    python_executable_raw = raw.get("python_executable")
    python_executable = Path(python_executable_raw).expanduser() if python_executable_raw else Path(sys.executable)

    frequency_days = int(raw.get("frequency_days", base.frequency_days))
    min_hours_between_runs = int(raw.get("min_hours_between_runs", base.min_hours_between_runs))
    max_audit_days = int(raw.get("max_audit_days", base.max_audit_days))
    if frequency_days < 1 or min_hours_between_runs < 1 or max_audit_days < 1:
        raise ValueError("frequency_days, min_hours_between_runs, and max_audit_days must be >= 1")

    extra_tags_raw = raw.get("extra_tags") or base.extra_tags
    extra_tags = [str(tag) for tag in extra_tags_raw] if isinstance(extra_tags_raw, list) else base.extra_tags

    return MetaSupervisorConfig(
        enabled=bool(raw.get("enabled", base.enabled)),
        meta_agent_root=meta_agent_root.resolve() if meta_agent_root else None,
        python_executable=python_executable.resolve(),
        project_id=str(raw.get("project_id", base.project_id)),
        frequency_days=frequency_days,
        min_hours_between_runs=min_hours_between_runs,
        require_bot_idle=bool(raw.get("require_bot_idle", base.require_bot_idle)),
        dry_run=bool(raw.get("dry_run", base.dry_run)),
        use_supervisor_runner=bool(raw.get("use_supervisor_runner", base.use_supervisor_runner)),
        task_title_prefix=str(raw.get("task_title_prefix", base.task_title_prefix)),
        extra_tags=extra_tags,
        max_audit_days=max_audit_days,
    )


def load_llm_supervisor_config(path: Path) -> LlmSupervisorConfig:
    """Load LLM supervisor configuration."""

    if not path.exists():
        trust = LlmSupervisorTrustPolicy(True, False, True, 0.2, 1.0)
        return LlmSupervisorConfig(
            enabled=False,
            api_url="https://api.openai.com/v1/chat/completions",
            model="gpt-4.1-mini",
            api_key_env="OPENAI_API_KEY_SUPERVISOR",
            check_interval_minutes=15,
            min_order_decisions=10,
            max_events_in_summary=50,
            max_trades_in_table=20,
            timeout_seconds=20,
            dry_run=True,
            trust_policy=trust,
        )

    raw = _load_yaml(path)

    trust_raw = raw.get("trust_policy", {}) or {}
    min_mul = float(trust_raw.get("min_multiplier", 0.2))
    max_mul = float(trust_raw.get("max_multiplier", 1.0))
    if not (0 < min_mul <= max_mul <= 1.0):
        raise ValueError("Invalid multiplier bounds in trust_policy")

    trust = LlmSupervisorTrustPolicy(
        allow_risk_multiplier=bool(trust_raw.get("allow_risk_multiplier", True)),
        allow_mode_switch=bool(trust_raw.get("allow_mode_switch", False)),
        allow_pause=bool(trust_raw.get("allow_pause", True)),
        min_multiplier=min_mul,
        max_multiplier=max_mul,
    )

    def _positive_int(name: str, default: int) -> int:
        val = int(raw.get(name, default))
        if val <= 0:
            raise ValueError(f"{name} must be positive")
        return val

    return LlmSupervisorConfig(
        enabled=bool(raw.get("enabled", False)),
        api_url=str(raw.get("api_url", "https://api.openai.com/v1/chat/completions")),
        model=str(raw.get("model", "gpt-4.1-mini")),
        api_key_env=str(raw.get("api_key_env", "OPENAI_API_KEY_SUPERVISOR")),
        check_interval_minutes=_positive_int("check_interval_minutes", 15),
        min_order_decisions=_positive_int("min_order_decisions", 10),
        max_events_in_summary=_positive_int("max_events_in_summary", 50),
        max_trades_in_table=_positive_int("max_trades_in_table", 20),
        timeout_seconds=_positive_int("timeout_seconds", 20),
        dry_run=bool(raw.get("dry_run", True)),
        trust_policy=trust,
    )


def _default_trend_config() -> TrendEvaluatorConfig:
    return TrendEvaluatorConfig(
        enabled=True,
        model="gpt-4.1-mini",
        temperature=0.2,
        timeout_seconds=1.5,
        history_window_minutes=15,
        include_volatility=True,
        include_signal_stats=True,
        max_calls_per_minute=20,
        cache_enabled=True,
        cache_ttl_seconds=120,
    )


def load_trend_evaluator_config(path: Path) -> TrendEvaluatorConfig:
    """Load configuration for the trend evaluator."""

    base = _default_trend_config()
    raw = _load_yaml(path) if path.exists() else {}
    inputs = raw.get("inputs", {}) or {}
    cache_raw = raw.get("cache", {}) or {}
    rate_raw = raw.get("rate_limit", {}) or {}

    return TrendEvaluatorConfig(
        enabled=bool(raw.get("enabled", base.enabled)),
        model=str(raw.get("model", base.model)),
        temperature=float(raw.get("temperature", base.temperature)),
        timeout_seconds=float(raw.get("timeout_ms", int(base.timeout_seconds * 1000))) / 1000.0,
        history_window_minutes=int(inputs.get("history_window_minutes", base.history_window_minutes)),
        include_volatility=bool(inputs.get("include_volatility", base.include_volatility)),
        include_signal_stats=bool(inputs.get("include_signal_stats", base.include_signal_stats)),
        max_calls_per_minute=int(rate_raw.get("max_calls_per_minute", base.max_calls_per_minute)),
        cache_enabled=bool(cache_raw.get("enabled", base.cache_enabled)),
        cache_ttl_seconds=int(cache_raw.get("ttl_seconds", base.cache_ttl_seconds)),
    )


def _default_market_risk_config() -> MarketRiskMonitorConfig:
    return MarketRiskMonitorConfig(
        enabled=True,
        model="gpt-4.1-mini",
        temperature=0.1,
        timeout_seconds=1.2,
        history_window_minutes=15,
        include_liquidations=False,
        include_orderbook_imbalance=True,
        risk_scale={"LOW": 0, "MEDIUM": 1, "HIGH": 2},
        max_calls_per_minute=20,
    )


def load_market_risk_config(path: Path) -> MarketRiskMonitorConfig:
    """Load configuration for the market risk monitor."""

    base = _default_market_risk_config()
    raw = _load_yaml(path) if path.exists() else {}
    inputs = raw.get("inputs", {}) or {}
    risk_scale_raw = raw.get("risk_scale", {}) or base.risk_scale
    rate_raw = raw.get("rate_limit", {}) or {}

    risk_scale = {str(k): int(v) for k, v in risk_scale_raw.items()}

    return MarketRiskMonitorConfig(
        enabled=bool(raw.get("enabled", base.enabled)),
        model=str(raw.get("model", base.model)),
        temperature=float(raw.get("temperature", base.temperature)),
        timeout_seconds=float(raw.get("timeout_ms", int(base.timeout_seconds * 1000))) / 1000.0,
        history_window_minutes=int(inputs.get("history_window_minutes", base.history_window_minutes)),
        include_liquidations=bool(inputs.get("include_liquidations", base.include_liquidations)),
        include_orderbook_imbalance=bool(inputs.get("include_orderbook_imbalance", base.include_orderbook_imbalance)),
        risk_scale=risk_scale,
        max_calls_per_minute=int(rate_raw.get("max_calls_per_minute", base.max_calls_per_minute)),
    )


def _default_behavior_config() -> TradingBehaviorConfig:
    return TradingBehaviorConfig(
        enabled=True,
        model="gpt-4.1-mini",
        temperature=0.2,
        timeout_seconds=1.5,
        history_trades=40,
        history_signals=60,
        max_calls_per_minute=10,
    )


def load_trading_behavior_config(path: Path) -> TradingBehaviorConfig:
    """Load configuration for the trading behavior analyzer."""

    base = _default_behavior_config()
    raw = _load_yaml(path) if path.exists() else {}
    history = raw.get("history", {}) or {}
    rate_raw = raw.get("rate_limit", {}) or {}

    return TradingBehaviorConfig(
        enabled=bool(raw.get("enabled", base.enabled)),
        model=str(raw.get("model", base.model)),
        temperature=float(raw.get("temperature", base.temperature)),
        timeout_seconds=float(raw.get("timeout_ms", int(base.timeout_seconds * 1000))) / 1000.0,
        history_trades=int(history.get("trades", base.history_trades)),
        history_signals=int(history.get("signals", base.history_signals)),
        max_calls_per_minute=int(rate_raw.get("max_calls_per_minute", base.max_calls_per_minute)),
    )


def load_snapshot_scheduler_config(path: Path) -> SnapshotSchedulerConfig:
    """Load snapshot scheduler configuration from supervisor.yaml."""

    raw = _load_yaml(path)
    snap = raw.get("snapshots", {}) or {}
    enabled = bool(snap.get("enabled", True))
    interval = int(snap.get("interval_minutes", 5))
    history = int(snap.get("history_window_minutes", 15))
    if interval <= 0 or history <= 0:
        raise ValueError("snapshot interval and history window must be positive")
    return SnapshotSchedulerConfig(enabled=enabled, interval_minutes=interval, history_window_minutes=history)


def load_dashboard_config(path: Path) -> DashboardConfig:
    """Load dashboard backend configuration."""

    if not path.exists():
        return DashboardConfig(
            enabled=True,
            max_events=200,
            events_types=["ORDER_DECISION", "ORDER_RESULT", "RISK_LIMIT_BREACH", "SUPERVISOR_SNAPSHOT", "STRATEGY_UPDATE"],
            pnl_window_minutes=60,
            max_snapshots=12,
            require_snapshot_recent_minutes=10,
            require_heartbeat_recent_seconds=60,
        )

    raw = _load_yaml(path)
    overview = raw.get("overview", {}) or {}
    health = raw.get("health", {}) or {}

    return DashboardConfig(
        enabled=bool(raw.get("enabled", True)),
        max_events=int(raw.get("max_events", 200)),
        events_types=[str(t) for t in raw.get("events_types", [])] if raw.get("events_types") is not None else [],
        pnl_window_minutes=int(overview.get("pnl_window_minutes", 60)),
        max_snapshots=int(overview.get("max_snapshots", 12)),
        require_snapshot_recent_minutes=int(health.get("require_snapshot_recent_minutes", 10)),
        require_heartbeat_recent_seconds=int(health.get("require_heartbeat_recent_seconds", 60)),
    )


def load_tsdb_config(path: Path) -> TsdbConfig:
    """Load TSDB configuration."""

    if not path.exists():
        return TsdbConfig(
            enabled=False,
            backend="none",
            flush_interval_seconds=2,
            batch_size=500,
            table_prefix="qe_",
            clickhouse_url="http://localhost:8123",
            clickhouse_database="quantumedge",
            clickhouse_user="default",
            clickhouse_password="",
            questdb_ilp_http_url="http://localhost:9000/imp",
            retry_max_retries=3,
            retry_base_backoff_ms=200,
            retry_max_backoff_ms=5000,
            backfill_enabled=False,
            backfill_from_days=1,
        )

    raw = _load_yaml(path)
    tables = raw.get("tables", {}) or {}
    ch = raw.get("clickhouse", {}) or {}
    retry = raw.get("retry", {}) or {}
    backfill = raw.get("backfill", {}) or {}

    return TsdbConfig(
        enabled=bool(raw.get("enabled", False)),
        backend=str(raw.get("backend", "none")).lower(),
        flush_interval_seconds=int(raw.get("flush_interval_seconds", 2)),
        batch_size=int(raw.get("batch_size", 500)),
        table_prefix=str(tables.get("prefix", "qe_")),
        clickhouse_url=str(ch.get("url", "http://localhost:8123")),
        clickhouse_database=str(ch.get("database", "quantumedge")),
        clickhouse_user=str(ch.get("user", "default")),
        clickhouse_password=str(ch.get("password", "")),
        questdb_ilp_http_url=str((raw.get("questdb") or {}).get("ilp_http_url", "http://localhost:9000/imp")),
        retry_max_retries=int(retry.get("max_retries", 3)),
        retry_base_backoff_ms=int(retry.get("base_backoff_ms", 200)),
        retry_max_backoff_ms=int(retry.get("max_backoff_ms", 5000)),
        backfill_enabled=bool(backfill.get("enabled", False)),
        backfill_from_days=int(backfill.get("from_days", 1)),
    )


def load_tsdb_retention_config(path: Path) -> TsdbRetentionConfig:
    """Load TSDB retention/rollup config."""

    if not path.exists():
        return TsdbRetentionConfig(
            enabled=False,
            raw_days=14,
            rollup_1m_days=90,
            rollup_1h_days=365,
            rollups_enabled=False,
            rollup_intervals=[],
        )
    raw = _load_yaml(path)
    retention = raw.get("retention_days", {}) or {}
    rollups = raw.get("rollups", {}) or {}
    intervals = rollups.get("intervals", []) or []
    return TsdbRetentionConfig(
        enabled=bool(raw.get("enabled", True)),
        raw_days=int(retention.get("raw", 14)),
        rollup_1m_days=int(retention.get("rollup_1m", 90)),
        rollup_1h_days=int(retention.get("rollup_1h", 365)),
        rollups_enabled=bool(rollups.get("enabled", True)),
        rollup_intervals=intervals,
    )
