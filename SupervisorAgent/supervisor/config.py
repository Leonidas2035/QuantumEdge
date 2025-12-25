"""Configuration loading utilities for SupervisorAgent."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, List

import yaml


@dataclass
class PathsConfig:
    """File system layout for SupervisorAgent and external dependencies."""

    qe_root: Path
    quantumedge_root: Path
    python_executable: Path
    meta_agent_root: Path
    logs_dir: Path
    runtime_dir: Path
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
    bot_entrypoint: str = "ai_scalper_bot/run_bot.py"
    bot_workdir: str = "ai_scalper_bot"
    bot_config: str = "config/bot.yaml"
    bot_env_file: str = ""
    bot_auto_start: bool = True
    bot_restart_enabled: bool = True
    bot_restart_max_retries: int = 5
    bot_restart_backoff_seconds: List[int] = field(default_factory=lambda: [1, 2, 5, 10, 30])
    policy_publish_interval_s: float = 5.0
    policy_ttl_sec: int = 30
    policy_file_path: str = "runtime/policy.json"
    policy_allow_trading: bool = True
    policy_mode: str = "normal"
    policy_size_multiplier: float = 1.0
    policy_cooldown_sec: int = 0
    policy_spread_max_bps: Optional[float] = None
    policy_max_daily_loss: Optional[float] = None
    policy_reason: str = "OK"
    policy_hysteresis_enter_cycles: int = 2
    policy_hysteresis_exit_cycles: int = 3
    policy_restart_rate: Optional[float] = 3.0
    policy_max_drawdown_abs: Optional[float] = None
    policy_loss_streak: int = 3
    policy_conservative_size_multiplier: float = 0.5
    policy_loss_streak_mode: str = "conservative"
    policy_volatility_hi: Optional[float] = None
    policy_llm_enabled: bool = False
    policy_llm_model: str = "gpt-4.1-mini"
    policy_llm_api_url: str = "https://api.openai.com/v1/chat/completions"
    policy_llm_api_key_env: str = "OPENAI_API_KEY_SUPERVISOR"
    policy_llm_timeout_sec: float = 4.0
    policy_llm_temperature: float = 0.1
    policy_llm_cb_failures: int = 3
    policy_llm_cb_window_sec: int = 300
    policy_llm_cb_open_sec: int = 120


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


def _resolve_path(value: Optional[str | Path], base: Path, default: Optional[Path] = None) -> Path:
    if value is None or value == "":
        return default.resolve() if default is not None else base.resolve()
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def load_paths_config(path: Path) -> PathsConfig:
    """Load filesystem-related configuration from YAML."""

    raw = _load_yaml(path)
    if isinstance(raw.get("paths"), dict):
        raw = raw["paths"]
    project_root = path.parent.parent.resolve()

    qe_root = _resolve_path(raw.get("qe_root") or os.getenv("QE_ROOT") or project_root, project_root)

    quantumedge_root = _resolve_path(
        raw.get("quantumedge_root"),
        qe_root,
        qe_root / "ai_scalper_bot",
    )

    python_executable_value = raw.get("python_executable") or sys.executable
    python_executable = Path(str(python_executable_value)).expanduser()

    meta_agent_root = _resolve_path(raw.get("meta_agent_root"), qe_root, qe_root / "meta_agent")

    logs_dir = _resolve_path(raw.get("logs_dir"), qe_root, qe_root / "logs")
    runtime_dir = _resolve_path(raw.get("runtime_dir"), qe_root, qe_root / "runtime")

    events_dir = _resolve_path(raw.get("events_dir"), qe_root, logs_dir / "events")

    reports_dir = _resolve_path(raw.get("reports_dir"), qe_root, project_root / "reports")

    return PathsConfig(
        qe_root=qe_root.resolve(),
        quantumedge_root=quantumedge_root.resolve(),
        python_executable=python_executable.resolve(),
        meta_agent_root=meta_agent_root.resolve(),
        logs_dir=logs_dir.resolve(),
        runtime_dir=runtime_dir.resolve(),
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

    env_port = os.getenv("SUPERVISOR_PORT") or os.getenv("QE_SUPERVISOR_PORT")
    heartbeat_port = int(env_port or raw.get("heartbeat_port", 8765))
    heartbeat_timeout_s = float(raw.get("heartbeat_timeout_s", 15))
    restart_max_attempts = int(raw.get("restart_max_attempts", 3))
    restart_backoff_s = float(raw.get("restart_backoff_s", 5))
    if heartbeat_port < 1 or heartbeat_port > 65535:
        raise ValueError("heartbeat_port must be between 1 and 65535")

    env_host = os.getenv("SUPERVISOR_HOST") or os.getenv("QE_SUPERVISOR_HOST")
    api_host = str(env_host or raw.get("api_host", "127.0.0.1"))
    bot_entrypoint = str(raw.get("bot_entrypoint", "ai_scalper_bot/run_bot.py"))
    bot_workdir = str(raw.get("bot_workdir", "ai_scalper_bot"))
    bot_config = str(raw.get("bot_config", "config/bot.yaml"))
    bot_section = raw.get("bot", {}) or {}
    bot_env_file = str(bot_section.get("env_file", "")) if isinstance(bot_section, dict) else ""
    bot_auto_start = bool(bot_section.get("auto_start", True))
    restart_section = bot_section.get("restart", {}) or {}
    bot_restart_enabled = bool(restart_section.get("enabled", True))
    bot_restart_max_retries = int(restart_section.get("max_retries", restart_max_attempts))
    if bot_restart_max_retries < 0:
        bot_restart_max_retries = 0
    backoff_raw = restart_section.get("backoff_seconds")
    if isinstance(backoff_raw, list) and backoff_raw:
        bot_restart_backoff_seconds = [int(val) for val in backoff_raw if int(val) > 0]
    elif backoff_raw is None:
        bot_restart_backoff_seconds = [int(restart_backoff_s)] if restart_backoff_s > 0 else [1, 2, 5, 10, 30]
    else:
        try:
            val = float(backoff_raw)
            bot_restart_backoff_seconds = [int(val)] if val > 0 else [1, 2, 5, 10, 30]
        except (TypeError, ValueError):
            bot_restart_backoff_seconds = [1, 2, 5, 10, 30]
    if not bot_restart_backoff_seconds:
        bot_restart_backoff_seconds = [1, 2, 5, 10, 30]

    policy_section = raw.get("policy", {}) or {}
    update_interval = policy_section.get("update_interval_sec", policy_section.get("publish_interval_s", 5))
    policy_publish_interval_s = float(update_interval)
    if policy_publish_interval_s <= 0:
        policy_publish_interval_s = 5.0
    policy_ttl_sec = int(policy_section.get("ttl_sec", 30))
    if policy_ttl_sec <= 0:
        policy_ttl_sec = 30
    policy_file_path = str(policy_section.get("file_path", "runtime/policy.json"))
    policy_allow_trading = bool(policy_section.get("allow_trading", True))
    policy_mode = str(policy_section.get("mode", "normal"))
    policy_size_multiplier = float(policy_section.get("size_multiplier", 1.0))
    if policy_size_multiplier < 0:
        policy_size_multiplier = 0.0
    policy_cooldown_sec = int(policy_section.get("cooldown_sec", 0))
    if policy_cooldown_sec < 0:
        policy_cooldown_sec = 0
    policy_spread_max_bps = policy_section.get("spread_max_bps")
    if policy_spread_max_bps is not None:
        policy_spread_max_bps = float(policy_spread_max_bps)
    policy_max_daily_loss = policy_section.get("max_daily_loss")
    if policy_max_daily_loss is not None:
        policy_max_daily_loss = float(policy_max_daily_loss)
    policy_reason = str(policy_section.get("reason", "OK"))
    hysteresis = policy_section.get("hysteresis", {}) or {}
    policy_hysteresis_enter_cycles = int(hysteresis.get("enter_cycles", 2))
    policy_hysteresis_exit_cycles = int(hysteresis.get("exit_cycles", 3))

    thresholds = policy_section.get("thresholds", {}) or {}
    policy_restart_rate = thresholds.get("restart_rate", 3.0)
    policy_restart_rate = float(policy_restart_rate) if policy_restart_rate is not None else None
    policy_max_drawdown_abs = thresholds.get("max_drawdown_abs")
    policy_max_drawdown_abs = float(policy_max_drawdown_abs) if policy_max_drawdown_abs is not None else None
    policy_loss_streak = int(thresholds.get("loss_streak", 3))
    policy_conservative_size_multiplier = float(thresholds.get("conservative_size_multiplier", 0.5))
    if policy_conservative_size_multiplier < 0:
        policy_conservative_size_multiplier = 0.0
    policy_loss_streak_mode = str(thresholds.get("loss_streak_mode", "conservative"))
    policy_volatility_hi = thresholds.get("volatility_hi")
    policy_volatility_hi = float(policy_volatility_hi) if policy_volatility_hi is not None else None

    llm_section = raw.get("llm", {}) or {}
    policy_llm_enabled = bool(llm_section.get("enabled", False))
    policy_llm_model = str(llm_section.get("model", "gpt-4.1-mini"))
    policy_llm_api_url = str(llm_section.get("api_url", "https://api.openai.com/v1/chat/completions"))
    policy_llm_api_key_env = str(llm_section.get("api_key_env", "OPENAI_API_KEY_SUPERVISOR"))
    policy_llm_timeout_sec = float(llm_section.get("timeout_sec", 4.0))
    policy_llm_temperature = float(llm_section.get("temperature", 0.1))
    cb = llm_section.get("circuit_breaker", {}) or {}
    policy_llm_cb_failures = int(cb.get("failures", 3))
    policy_llm_cb_window_sec = int(cb.get("window_sec", 300))
    policy_llm_cb_open_sec = int(cb.get("open_sec", 120))

    return SupervisorConfig(
        mode=mode,
        heartbeat_port=heartbeat_port,
        heartbeat_timeout_s=heartbeat_timeout_s,
        restart_max_attempts=restart_max_attempts,
        restart_backoff_s=restart_backoff_s,
        exchange=str(raw.get("exchange", "")),
        api_enabled=bool(raw.get("api_enabled", True)),
        api_host=api_host,
        api_auth_token=str(raw.get("api_auth_token", "")),
        bot_entrypoint=bot_entrypoint,
        bot_workdir=bot_workdir,
        bot_config=bot_config,
        bot_env_file=bot_env_file,
        bot_auto_start=bot_auto_start,
        bot_restart_enabled=bot_restart_enabled,
        bot_restart_max_retries=bot_restart_max_retries,
        bot_restart_backoff_seconds=bot_restart_backoff_seconds,
        policy_publish_interval_s=policy_publish_interval_s,
        policy_ttl_sec=policy_ttl_sec,
        policy_file_path=policy_file_path,
        policy_allow_trading=policy_allow_trading,
        policy_mode=policy_mode,
        policy_size_multiplier=policy_size_multiplier,
        policy_cooldown_sec=policy_cooldown_sec,
        policy_spread_max_bps=policy_spread_max_bps,
        policy_max_daily_loss=policy_max_daily_loss,
        policy_reason=policy_reason,
        policy_hysteresis_enter_cycles=policy_hysteresis_enter_cycles,
        policy_hysteresis_exit_cycles=policy_hysteresis_exit_cycles,
        policy_restart_rate=policy_restart_rate,
        policy_max_drawdown_abs=policy_max_drawdown_abs,
        policy_loss_streak=policy_loss_streak,
        policy_conservative_size_multiplier=policy_conservative_size_multiplier,
        policy_loss_streak_mode=policy_loss_streak_mode,
        policy_volatility_hi=policy_volatility_hi,
        policy_llm_enabled=policy_llm_enabled,
        policy_llm_model=policy_llm_model,
        policy_llm_api_url=policy_llm_api_url,
        policy_llm_api_key_env=policy_llm_api_key_env,
        policy_llm_timeout_sec=policy_llm_timeout_sec,
        policy_llm_temperature=policy_llm_temperature,
        policy_llm_cb_failures=policy_llm_cb_failures,
        policy_llm_cb_window_sec=policy_llm_cb_window_sec,
        policy_llm_cb_open_sec=policy_llm_cb_open_sec,
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
