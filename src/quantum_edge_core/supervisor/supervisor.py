"""CLI entrypoint for SupervisorAgent."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import signal
import sys
import time
import uuid
from datetime import datetime, date, timezone
from pathlib import Path
from typing import Optional, Mapping, Any, Dict, List
import threading
import zmq

from quantum_edge_core.supervisor.supervisor.config import (
    load_paths_config,
    load_supervisor_config,
    load_risk_config,
    load_autopilot_config,
    load_llm_supervisor_config,
    load_meta_supervisor_config,
    load_trend_evaluator_config,
    load_market_risk_config,
    load_trading_behavior_config,
    load_snapshot_scheduler_config,
    load_dashboard_config,
    load_lockbot_config,
    load_tsdb_config,
    load_tsdb_retention_config,
    PathsConfig,
    SupervisorConfig,
    RiskConfig,
    LlmSupervisorConfig,
    MetaSupervisorConfig,
    TrendEvaluatorConfig,
    MarketRiskMonitorConfig,
    TradingBehaviorConfig,
    SnapshotSchedulerConfig,
    DashboardConfig,
    LockbotControlConfig,
    TsdbConfig,
    TsdbRetentionConfig,
    AutopilotConfig,
)
from quantum_edge_core.supervisor.supervisor.heartbeat import HeartbeatServer, HeartbeatPayload
from quantum_edge_core.supervisor.supervisor.logging_setup import setup_logging
from quantum_edge_core.supervisor.supervisor.config_loader import load_processes_spec
from quantum_edge_core.supervisor.supervisor.process_manager import ProcessManager, ProcessInfo
from quantum_edge_core.supervisor.supervisor.risk_engine import (
    HardRiskEngine,
    RiskDecision,
    OrderRequest,
    OrderSide,
    OrderType,
)
from quantum_edge_core.supervisor.supervisor import state as state_utils
from quantum_edge_core.supervisor.supervisor.events import (
    BaseEvent,
    EventLogger,
    EventType,
    new_run_id,
    prune_event_logs,
)
from quantum_edge_core.supervisor.supervisor.audit_report import (
    load_events_for_date,
    compute_stats,
    render_markdown_report,
)
from quantum_edge_core.supervisor.supervisor.llm_supervisor import LlmSupervisor

from quantum_edge_core.supervisor.supervisor.llm.google_client import GoogleClient
from quantum_edge_core.supervisor.supervisor.llm.trend_evaluator import TrendEvaluator
from quantum_edge_core.supervisor.supervisor.llm.market_risk_monitor import MarketRiskMonitor
from quantum_edge_core.supervisor.supervisor.llm.trading_behavior_analyzer import TradingBehaviorAnalyzer
from quantum_edge_core.supervisor.supervisor.meta_supervisor import MetaSupervisorRunner, MetaSupervisorContext
from quantum_edge_core.supervisor.supervisor.api_server import ApiServer, ApiServerConfig
from quantum_edge_core.supervisor.supervisor.snapshot_models import SnapshotReport
from quantum_edge_core.supervisor.supervisor.tasks.snapshot_scheduler import SnapshotScheduler
from quantum_edge_core.supervisor.supervisor.dashboard.service import DashboardService
from quantum_edge_core.supervisor.supervisor.dashboard.audit_log import DashboardAuditLogger
from quantum_edge_core.supervisor.supervisor.dashboard.state_store import DashboardStateStore
from quantum_edge_core.supervisor.supervisor.tsdb import (
    NoopTimeseriesStore,
    ClickHouseTimeseriesStore,
    QuestDbTimeseriesStore,
    TsdbWriter,
)
from quantum_edge_core.supervisor.supervisor.tsdb.maintenance import apply_retention_and_rollups
from quantum_edge_core.supervisor.supervisor.tsdb.query import (
    build_timeseries_query,
    derive_questdb_query_url,
    questdb_query,
    sanitize_symbol,
)
from quantum_edge_core.supervisor.supervisor.ingest.pipeline import IngestPipeline
from quantum_edge_core.supervisor.supervisor.ingest.parsers import parse_metrics_file, parse_event_line
from policy.policy_contract import policy_fingerprint, POLICY_VERSION
from policy.policy_publisher import PolicyPublisher
from policy.policy_engine import PolicyEngine, PolicyEngineConfig, HysteresisConfig
from policy.heuristics import HeuristicThresholds
from monitoring.api import TelemetryManager, TelemetryConfig
from quantum_edge_core.supervisor.supervisor.stats import StatsAggregator
from quantum_edge_core.supervisor.supervisor.run_context import RunContext
from quantum_edge_core.supervisor.supervisor.regime_sm import (
    RegimeStateMachine,
    RegimeConfig,
    DirectivesConfig,
    load_regime_config,
    load_directives_config,
)
from quantum_edge_core.supervisor.supervisor.guards import (
    GuardEvaluator,
    GuardResult,
    GuardConfig,
    load_guard_config,
)
from quantum_edge_core.supervisor.supervisor.action_ledger import ActionLedger
from quantum_edge_core.supervisor.supervisor.policy_store import resolve_active_policy_path
from quantum_edge_core.supervisor.supervisor.autopilot.cli import (
    build_controller,
    autopilot_enable,
    autopilot_set_target_state,
    autopilot_status,
    policy_list,
    policy_rollout,
    policy_rollback,
)
from quantum_edge_core.supervisor.supervisor.autopilot.policy_manager import PolicyManager
from quantum_edge_core.supervisor.supervisor.alerts.rules import load_alert_rules
from quantum_edge_core.supervisor.supervisor.alerts.storage import AlertStorage
from quantum_edge_core.supervisor.supervisor.alerts.engine import AlertEngine, AlertResult
from quantum_edge_core.supervisor.supervisor.security import is_path_allowed, validate_kill_switch_challenge
from quantum_edge_core.supervisor.supervisor.process_spec import ProcessSpec
from quantum_edge_core.supervisor.supervisor.lockbot.control_client import LockbotControlClient
from quantum_edge_core.supervisor.supervisor.lockbot.models import PolicyRunnerConfig, load_lockbot_policy_config
from quantum_edge_core.supervisor.supervisor.lockbot.policy_runner import LockbotPolicyRunner
from monitor import ZmqHeartbeatSubscriber

try:
    from tools.qe_config import get_qe_paths
except Exception:  # pragma: no cover - fallback for legacy runs
    get_qe_paths = None


class ZmqPolicyPublisher:
    """Publishes policy updates via ZMQ."""

    def __init__(self, endpoint: str):
        self.ctx = zmq.Context()
        self.socket = self.ctx.socket(zmq.PUB)
        self.socket.setsockopt(zmq.RCVTIMEO, 2000)
        self.socket.setsockopt(zmq.SNDTIMEO, 2000)
        try:
            self.socket.bind(endpoint)
            logging.getLogger(__name__).info(f"Policy PUB bound to {endpoint}")
        except zmq.ZMQError as e:
            logging.getLogger(__name__).error(f"Failed to bind Policy PUB: {e}")

    def publish(self, payload: Dict[str, Any]):
        try:
            msg = json.dumps(payload).encode("utf-8")
            self.socket.send_multipart([b"policy", msg])
        except Exception:
            pass

    def close(self):
        self.socket.close()
        self.ctx.term()


class SupervisorApp:
    """High-level facade for supervisor commands."""

    def __init__(
        self,
        paths: PathsConfig,
        config: SupervisorConfig,
        risk_config: RiskConfig,
        llm_config: LlmSupervisorConfig,
        trend_config: TrendEvaluatorConfig,
        market_risk_config: MarketRiskMonitorConfig,
        behavior_config: TradingBehaviorConfig,
        snapshot_config: SnapshotSchedulerConfig,
        meta_config: MetaSupervisorConfig,
        dashboard_config: DashboardConfig,
        lockbot_cfg: LockbotControlConfig,
        lockbot_policy_cfg: PolicyRunnerConfig,
        tsdb_config: TsdbConfig,
        tsdb_retention: TsdbRetentionConfig,
        regime_cfg: RegimeConfig,
        guard_cfg: GuardConfig,
        directives_cfg: DirectivesConfig,
        autopilot_cfg: AutopilotConfig,
        process_specs: Dict[str, ProcessSpec],
        project_root: Path,
        logger: Optional[logging.Logger] = None,
        telemetry_port: int = 5557,
        policy_port: int = 5558,
        expected_bot_id: str = "ai_scalper_bot",
    ) -> None:
        self.paths = paths
        self.config = config
        self.telemetry_port = telemetry_port
        self.policy_port = policy_port
        self.expected_bot_id = expected_bot_id
        self.risk_config = risk_config
        self.llm_config = llm_config
        self.trend_config = trend_config
        self.market_risk_config = market_risk_config
        self.behavior_config = behavior_config
        self.snapshot_config = snapshot_config
        self.meta_config = meta_config
        self.project_root = project_root
        self.tsdb_config = tsdb_config
        self.dashboard_config = dashboard_config
        self.lockbot_cfg = lockbot_cfg
        self.lockbot_policy_cfg = lockbot_policy_cfg
        self.logger = logger or logging.getLogger(__name__)
        self.autopilot_cfg = autopilot_cfg
        self.process_specs = process_specs
        self.state_dir = paths.runtime_dir / "supervisor"
        self.state_dir.mkdir(parents=True, exist_ok=True)

        events_path = paths.events_dir / f"events_{date.today().isoformat()}.jsonl"
        self.snapshots_dir = paths.logs_dir / "snapshots"
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = new_run_id()
        self._start_ts = time.time()
        self.event_logger = EventLogger(
            events_path,
            self.logger,
            snapshots_dir=self.snapshots_dir,
            run_id=self.run_id,
        )
        prune_event_logs(paths.events_dir, config.events_retention_days, self.logger)
        # TSDB wiring
        self.tsdb_backend = "none"
        self.tsdb_writer = self._build_tsdb_writer(tsdb_config)
        if self.tsdb_writer:
            self.tsdb_writer.start()
            self.event_logger.tsdb_writer = self.tsdb_writer
        self.heartbeat_server = HeartbeatServer(config.heartbeat_timeout_s)
        risk_state = state_utils.load_risk_state(self.state_dir, today=date.today())
        self.risk_engine = HardRiskEngine(
            risk_config,
            risk_state,
            self.logger,
            self.event_logger,
            llm_config.trust_policy,
        )
        self.process_manager = ProcessManager(
            paths,
            config,
            self.state_dir,
            self.event_logger,
            self.logger,
            processes=process_specs,
            run_id=self.run_id,
        )

        # Enforce Google Gemini as default if legacy GPT models are configured
        for cfg in [llm_config, trend_config, market_risk_config, behavior_config]:
            if hasattr(cfg, "model") and "gpt" in cfg.model.lower():
                cfg.model = "gemini-2.0-flash"

        self.llm_client = GoogleClient(logger=self.logger)
        self.llm_supervisor = LlmSupervisor(
            llm_config,
            risk_config,
            paths.events_dir,
            self.logger,
            self.event_logger,
            chat_client=self.llm_client,
        )
        self.trend_evaluator = TrendEvaluator(
            trend_config, self.llm_client, self.logger
        )
        self.market_risk_monitor = MarketRiskMonitor(
            market_risk_config, self.llm_client, self.logger
        )
        self.behavior_analyzer = TradingBehaviorAnalyzer(
            behavior_config, self.llm_client, self.logger
        )
        snapshot_state_path = self.state_dir / "last_snapshot.json"
        self.snapshot_scheduler = SnapshotScheduler(
            snapshot_config,
            paths.events_dir,
            self.event_logger,
            self.trend_evaluator,
            self.market_risk_monitor,
            self.behavior_analyzer,
            snapshot_state_path,
            self.logger,
        )
        self.meta_supervisor_state_path = self.state_dir / "meta_supervisor_state.json"
        # Dashboard service (legacy views)
        self.dashboard_service = DashboardService(
            cfg={
                "enabled": dashboard_config.enabled,
                "max_events": dashboard_config.max_events,
                "events_types": dashboard_config.events_types,
                "overview": {
                    "pnl_window_minutes": dashboard_config.pnl_window_minutes,
                    "max_snapshots": dashboard_config.max_snapshots,
                },
                "health": {
                    "require_snapshot_recent_minutes": dashboard_config.require_snapshot_recent_minutes,
                    "require_heartbeat_recent_seconds": dashboard_config.require_heartbeat_recent_seconds,
                },
            },
            events_dir=paths.events_dir,
            heartbeat_server=self.heartbeat_server,
            snapshot_provider=self.snapshot_scheduler,
            strategy_state_path=self.state_dir / "active_strategy_mode.json",
            logger=self.logger,
        )
        self.tsdb_retention = tsdb_retention
        api_config = ApiServerConfig(
            host=config.api_host,
            port=config.heartbeat_port,
            auth_token=config.api_auth_token,
        )
        self.api_server = (
            ApiServer(api_config, self, self.logger) if config.api_enabled else None
        )
        self._lock = threading.Lock()
        policy_file = Path(config.policy_file_path)
        if not policy_file.is_absolute():
            policy_file = self.paths.qe_root / policy_file
        thresholds = HeuristicThresholds(
            max_daily_loss=config.policy_max_daily_loss,
            max_drawdown_abs=config.policy_max_drawdown_abs,
            loss_streak=config.policy_loss_streak,
            spread_max_bps=config.policy_spread_max_bps,
            volatility_hi=config.policy_volatility_hi,
            restart_rate=config.policy_restart_rate,
            conservative_size_multiplier=config.policy_conservative_size_multiplier,
            loss_streak_mode=config.policy_loss_streak_mode,
        )
        engine_cfg = PolicyEngineConfig(
            update_interval_sec=float(config.policy_publish_interval_s),
            ttl_sec=config.policy_ttl_sec,
            cooldown_sec=config.policy_cooldown_sec,
            thresholds=thresholds,
            hysteresis=HysteresisConfig(
                enter_cycles=config.policy_hysteresis_enter_cycles,
                exit_cycles=config.policy_hysteresis_exit_cycles,
            ),
            llm_enabled=config.policy_llm_enabled,
            llm_model=config.policy_llm_model,
            llm_api_url=config.policy_llm_api_url,
            llm_api_key_env=config.policy_llm_api_key_env,
            llm_timeout_sec=config.policy_llm_timeout_sec,
            llm_temperature=config.policy_llm_temperature,
            cb_failures=config.policy_llm_cb_failures,
            cb_window_sec=config.policy_llm_cb_window_sec,
            cb_open_sec=config.policy_llm_cb_open_sec,
            policy_state_path=self.paths.runtime_dir / "policy_state.json",
        )
        telemetry_persist = (
            Path(config.telemetry_persist_path)
            if config.telemetry_persist_path
            else None
        )
        if telemetry_persist and not telemetry_persist.is_absolute():
            telemetry_persist = (self.paths.qe_root / telemetry_persist).resolve()
        telemetry_cfg = TelemetryConfig(
            max_event_size_kb=config.telemetry_max_event_size_kb,
            max_events_in_memory=config.telemetry_max_events_in_memory,
            persist_path=str(telemetry_persist) if telemetry_persist else None,
            alerts_thresholds=config.telemetry_alerts_thresholds,
            alerts_cooldown_sec=config.telemetry_alerts_cooldown_sec,
        )
        self.telemetry = TelemetryManager(telemetry_cfg)
        self.policy_engine = PolicyEngine(
            engine_cfg,
            self.paths,
            self.process_manager,
            self.risk_engine,
            self.logger,
            telemetry_manager=self.telemetry,
        )
        self.policy_publisher = PolicyPublisher(policy_file, self.logger)
        self.policy_publish_interval_s = float(config.policy_publish_interval_s)
        self._last_policy_fingerprint: Optional[str] = None
        self._current_policy = None
        self.run_context: Optional[RunContext] = None
        self.stats: Optional[StatsAggregator] = None
        self._stop_requested = False
        self._stop_reason: Optional[str] = None
        self.regime_sm = RegimeStateMachine(regime_cfg)
        self.guard_evaluator = GuardEvaluator(guard_cfg)
        self.regime_cfg = regime_cfg
        self.guard_cfg = guard_cfg
        self.directives_cfg = directives_cfg
        self.autopilot = build_controller(self, autopilot_cfg, paths)
        self.action_ledger: Optional[ActionLedger] = None
        self._directives_last_hash: Optional[str] = None
        alerts_path = project_root / "SupervisorAgent" / "config" / "alerts.yaml"
        if not alerts_path.exists():
            fallback = project_root / "config" / "alerts.yaml"
            if fallback.exists():
                alerts_path = fallback
        alert_rules = load_alert_rules(alerts_path)
        self.alert_storage = AlertStorage(self.paths.runtime_dir / "alerts")
        self.alert_engine = AlertEngine(alert_rules, self.alert_storage)
        dashboard_audit_path = self.paths.runtime_dir / "dashboard" / "audit.jsonl"
        self.dashboard_audit_logger = DashboardAuditLogger(
            dashboard_audit_path, self.logger
        )
        self.dashboard_store = DashboardStateStore(
            audit_logger=self.dashboard_audit_logger,
            alert_engine=self.alert_engine,
            telemetry_stale_ms=int(
                getattr(dashboard_config, "telemetry_stale_ms", 5000)
            ),
            cancel_window_sec=int(getattr(dashboard_config, "cancel_window_sec", 60)),
            cancel_storm_threshold=int(
                getattr(dashboard_config, "cancel_storm_threshold", 20)
            ),
            dca_stuck_sell_ms=int(
                getattr(dashboard_config, "dca_stuck_sell_ms", 60000)
            ),
            alert_eval_interval_sec=int(
                getattr(dashboard_config, "alert_eval_interval_sec", 5)
            ),
        )
        self._alert_eval_interval_s = 10
        self._last_alert_eval_ts = 0.0
        self._last_alert_result: Optional[AlertResult] = None
        self._kill_switch_challenge: Optional[Dict[str, Any]] = None
        self.lockbot_client: Optional[LockbotControlClient] = None
        if self.lockbot_cfg.enabled:
            self.lockbot_client = LockbotControlClient(self.lockbot_cfg, self.logger)
            self.lockbot_client.start()
        self.lockbot_policy_runner: Optional[LockbotPolicyRunner] = None
        if self.lockbot_policy_cfg.enabled:
            if not self.lockbot_client:
                self.logger.warning(
                    "Lockbot policy enabled but lockbot control client is disabled."
                )
            else:
                self.lockbot_policy_runner = LockbotPolicyRunner(
                    self.lockbot_policy_cfg, self.lockbot_client, self.logger
                )

        self.expected_id = expected_bot_id

        # Initialize ZMQ Heartbeat Subscriber
        self.heartbeat_subscriber = ZmqHeartbeatSubscriber(
            endpoint=f"tcp://127.0.0.1:{self.telemetry_port}",
            expected_id=self.expected_id,
        )

        # Initialize ZMQ Policy Publisher
        self.zmq_policy_publisher = ZmqPolicyPublisher(f"tcp://*:{self.policy_port}")

    def _build_tsdb_writer(self, tsdb_config: TsdbConfig) -> Optional[TsdbWriter]:
        self.tsdb_backend = "none"
        if not tsdb_config.enabled or tsdb_config.backend == "none":
            return None
        store = None
        try:
            if tsdb_config.backend == "clickhouse":
                store = ClickHouseTimeseriesStore(
                    url=tsdb_config.clickhouse_url,
                    database=tsdb_config.clickhouse_database,
                    user=tsdb_config.clickhouse_user,
                    password=tsdb_config.clickhouse_password,
                    table_prefix=tsdb_config.table_prefix,
                    retry_cfg={
                        "max_retries": tsdb_config.retry_max_retries,
                        "base_backoff_ms": tsdb_config.retry_base_backoff_ms,
                        "max_backoff_ms": tsdb_config.retry_max_backoff_ms,
                    },
                    logger=self.logger,
                )
            elif tsdb_config.backend == "questdb":
                store = QuestDbTimeseriesStore(
                    ilp_http_url=tsdb_config.questdb_ilp_http_url,
                    retry_cfg={
                        "max_retries": tsdb_config.retry_max_retries,
                        "base_backoff_ms": tsdb_config.retry_base_backoff_ms,
                        "max_backoff_ms": tsdb_config.retry_max_backoff_ms,
                    },
                    logger=self.logger,
                )
        except Exception as exc:  # pylint: disable=broad-except
            self.logger.warning(
                "TSDB backend init failed; continuing in Memory-Only mode without TSDB: %s",
                exc,
            )
            store = None
        if store is None:
            return None
        self.tsdb_backend = tsdb_config.backend
        return TsdbWriter(
            store=store,
            flush_interval_seconds=tsdb_config.flush_interval_seconds,
            batch_size=tsdb_config.batch_size,
            logger=self.logger,
        )

    def start(self) -> None:
        name = self.process_manager.default_name
        if not name:
            self.logger.error("No default process configured for start().")
            return
        info = self.process_manager.start(name)
        self.logger.info("Process '%s' started with PID %s", name, info.pid)

    def stop(self) -> None:
        name = self.process_manager.default_name
        if not name:
            self.logger.error("No default process configured for stop().")
            return
        self.process_manager.stop(name)
        self.logger.info("Process '%s' stopped.", name)

    def restart(self) -> None:
        name = self.process_manager.default_name
        if not name:
            self.logger.error("No default process configured for restart().")
            return
        info = self.process_manager.restart(name)
        self.logger.info("Process '%s' restarted with PID %s", name, info.pid)

    def get_bot_status(self) -> Dict[str, Any]:
        return self.process_manager.get_status_payload()

    def get_system_status(self) -> Dict[str, Any]:
        uptime_s = time.time() - self._start_ts if hasattr(self, "_start_ts") else None
        return {
            "run_id": self.run_id,
            "uptime_s": uptime_s,
            "policy_version": POLICY_VERSION,
            "processes": self.process_manager.status_all(),
        }

    def start_process(self, name: str) -> Dict[str, Any]:
        info = self.process_manager.start(name)
        status = self.process_manager.status(name).to_dict()
        status["pid"] = info.pid
        return status

    def stop_process(self, name: str) -> Dict[str, Any]:
        self.process_manager.stop(name)
        return self.process_manager.status(name).to_dict()

    def restart_process(self, name: str) -> Dict[str, Any]:
        info = self.process_manager.restart(name)
        status = self.process_manager.status(name).to_dict()
        status["pid"] = info.pid
        return status

    def get_events_tail(
        self,
        limit: int = 200,
        types: Optional[List[str]] = None,
        since_ts_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        from quantum_edge_core.supervisor.supervisor.events import tail_events

        events = tail_events(
            self.paths.events_dir / f"events_{date.today().isoformat()}.jsonl",
            limit=limit,
            types=types,
            since_ts_ms=since_ts_ms,
        )
        return {"events": events}

    def log_api_call(
        self,
        method: str,
        path: str,
        status_code: int,
        duration_ms: int,
        trace_id: Optional[str],
    ) -> None:
        event = BaseEvent(
            ts=datetime.now(timezone.utc),
            type=EventType.API_CALL,
            source="ApiServer",
            data={
                "method": method,
                "path": path,
                "status_code": status_code,
                "duration_ms": duration_ms,
            },
            severity="INFO" if status_code < 400 else "WARN",
            run_id=self.run_id,
            trace_id=trace_id,
        )
        self.event_logger.log_event(event)

    def get_policy_payload(self) -> Dict[str, Any]:
        policy = self._current_policy or self.policy_engine.current_policy()
        return policy.to_dict()

    def get_policy_debug(self) -> Dict[str, Any]:
        return self.policy_engine.debug_payload()

    def ingest_telemetry_event(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        event = self.telemetry.ingest(payload)
        try:
            self.dashboard_store.ingest_event(payload)
        except Exception as exc:
            self.logger.debug("Dashboard ingest failed: %s", exc)
        return event

    def get_telemetry_summary(self) -> Dict[str, Any]:
        return self.telemetry.summary()

    def get_telemetry_events(self, limit: int = 200) -> List[Dict[str, Any]]:
        return self.telemetry.events(limit=limit)

    def get_telemetry_alerts(self) -> Dict[str, Any]:
        return self.telemetry.alerts_payload()

    def _publish_policy(self) -> None:
        try:
            policy = self.policy_engine.evaluate()
            if not self.policy_publisher.publish(policy):
                return
            self._current_policy = policy
            self.telemetry.record_policy(policy.to_dict())
            fingerprint = policy_fingerprint(policy)
            if fingerprint != self._last_policy_fingerprint:
                self.logger.info(
                    "Policy updated: mode=%s allow_trading=%s ttl=%s size_multiplier=%.3f reason=%s hash=%s",
                    policy.mode,
                    policy.allow_trading,
                    policy.ttl_sec,
                    policy.size_multiplier,
                    policy.reason,
                    fingerprint[:12],
                )
                self._last_policy_fingerprint = fingerprint
        except Exception:
            self.logger.exception("Policy publish failed")

    def start_bot(self) -> Dict[str, Any]:
        try:
            name = self.process_manager.default_name
            if not name:
                raise RuntimeError("No default process configured.")
            self.process_manager.start(name)
        except Exception as exc:
            self.logger.error("Bot start failed: %s", exc)
        return self.get_bot_status()

    def stop_bot(self) -> Dict[str, Any]:
        name = self.process_manager.default_name
        if name:
            self.process_manager.stop(name)
        return self.get_bot_status()

    def restart_bot(self) -> Dict[str, Any]:
        try:
            name = self.process_manager.default_name
            if not name:
                raise RuntimeError("No default process configured.")
            self.process_manager.restart(name)
        except Exception as exc:
            self.logger.error("Bot restart failed: %s", exc)
        return self.get_bot_status()

    def autopilot_status(self) -> Dict[str, Any]:
        return self.autopilot.status()

    def autopilot_set_enabled(self, enabled: bool) -> Dict[str, Any]:
        override_path = self.paths.runtime_dir / "autopilot" / "override.json"
        return autopilot_enable(override_path, enabled, audit=self.autopilot.audit)

    def autopilot_set_target_state(self, target_state: str) -> Dict[str, Any]:
        override_path = self.paths.runtime_dir / "autopilot" / "override.json"
        return autopilot_set_target_state(
            override_path, target_state, audit=self.autopilot.audit
        )

    def policy_manager_for(self, symbol: Optional[str] = None):
        symbol = symbol or self.autopilot_cfg.policy_symbol
        artifacts_dir = Path(self.autopilot_cfg.policy_artifacts_dir)
        if not artifacts_dir.is_absolute():
            artifacts_dir = (self.paths.qe_root / artifacts_dir).resolve()
        if symbol:
            artifacts_dir = artifacts_dir / symbol
        runtime_dir = Path(self.autopilot_cfg.policy_runtime_dir)
        if not runtime_dir.is_absolute():
            runtime_dir = (self.paths.qe_root / runtime_dir).resolve()
        history_dir = self.paths.runtime_dir / "policy_rollouts" / symbol
        return PolicyManager(
            artifacts_dir,
            runtime_dir,
            history_dir,
            self.autopilot_cfg.policy_history_keep,
        )

    def policy_list_payload(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        manager = self.policy_manager_for(symbol)
        return policy_list(manager)

    def policy_rollout_payload(
        self, symbol: Optional[str], policy_path: str
    ) -> Dict[str, Any]:
        symbol = symbol or self.autopilot_cfg.policy_symbol
        manager = self.policy_manager_for(symbol)
        candidate = Path(policy_path)
        if not candidate.is_absolute():
            candidate = (self.paths.qe_root / candidate).resolve()
        base_artifacts = Path(self.autopilot_cfg.policy_artifacts_dir)
        if not base_artifacts.is_absolute():
            base_artifacts = (self.paths.qe_root / base_artifacts).resolve()
        if symbol:
            base_artifacts = base_artifacts / symbol
        base_rollouts = self.paths.runtime_dir / "policy_rollouts" / symbol
        if not (
            is_path_allowed(candidate, base_artifacts)
            or is_path_allowed(candidate, base_rollouts)
        ):
            raise ValueError("policy_path_not_allowed")
        return policy_rollout(
            manager, candidate, reason="manual_rollout", audit=self.autopilot.audit
        )

    def policy_rollback_payload(self, symbol: Optional[str]) -> Dict[str, Any]:
        manager = self.policy_manager_for(symbol)
        return policy_rollback(
            manager, reason="manual_rollback", audit=self.autopilot.audit
        )

    def get_kill_switch_challenge(self) -> Dict[str, Any]:
        challenge_id = str(uuid.uuid4())
        expires_at = time.time() + 120
        self._kill_switch_challenge = {
            "challenge_id": challenge_id,
            "expires_at": expires_at,
        }
        return {"challenge_id": challenge_id, "expires_at": expires_at}

    def apply_kill_switch(self, enabled: bool, challenge_id: str) -> Dict[str, Any]:
        error = validate_kill_switch_challenge(
            self._kill_switch_challenge, challenge_id, time.time()
        )
        if error:
            raise ValueError(error)
        kill_switch_path = self.paths.quantumedge_root / "state" / "kill_switch.json"
        kill_switch_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "enabled": bool(enabled),
            "reason": "manual_dashboard",
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        kill_switch_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self.autopilot.audit.log(
            {
                "action": "KILL_SWITCH",
                "applied": True,
                "enabled": bool(enabled),
                "correlation_id": str(uuid.uuid4()),
            }
        )
        return payload

    def alerts_snapshot(self) -> Dict[str, Any]:
        now = time.time()
        if now - self._last_alert_eval_ts >= self._alert_eval_interval_s:
            self._evaluate_alerts()
        if self._last_alert_result:
            return {
                "active": self._last_alert_result.active,
                "recent": self._last_alert_result.recent,
            }
        return {"active": [], "recent": []}

    def alerts_ack(self, alert_id: str, note: str) -> Dict[str, Any]:
        ok = self.alert_engine.ack(alert_id, note)
        return {"acknowledged": ok}

    def alerts_silence(self, rule: str, minutes: int) -> Dict[str, Any]:
        until = self.alert_engine.silence(rule, minutes)
        return {"silenced_until": until}

    def audit_recent(self, limit: int = 200) -> List[Dict[str, Any]]:
        return self.dashboard_audit_logger.read(limit=limit)

    def status(self) -> None:
        running = self.process_manager.is_running()
        info = self.process_manager.get_info()
        self._print_status(running, info)

    def run_foreground(self) -> None:
        """Run supervisor loop, restarting the child if it dies."""

        next_llm_check_at = 0
        snapshot_interval = (
            self.snapshot_config.interval_minutes * 60
            if self.snapshot_config.enabled
            else None
        )
        next_snapshot_at = (
            time.time() + snapshot_interval if snapshot_interval else float("inf")
        )
        next_policy_publish_at = 0.0
        stats_interval = int(self.config.telemetry_stats_snapshot_interval_s or 30)
        if stats_interval <= 0:
            stats_interval = 30
        next_stats_at = time.time() + stats_interval
        directives_interval = int(getattr(self.directives_cfg, "update_interval_s", 10))
        if directives_interval <= 0:
            directives_interval = 10
        next_directives_at = time.time()
        autopilot_interval = int(
            getattr(self.autopilot_cfg, "check_interval_sec", 10) or 10
        )
        if autopilot_interval <= 0:
            autopilot_interval = 10
        next_autopilot_at = time.time() + autopilot_interval
        alerts_interval = int(getattr(self, "_alert_eval_interval_s", 10) or 10)
        if alerts_interval <= 0:
            alerts_interval = 10
        next_alerts_at = time.time() + alerts_interval
        episode_tags = (
            getattr(self, "_episode_tags", {}) if hasattr(self, "_episode_tags") else {}
        )
        self.run_context = RunContext.create(
            project_root=self.project_root,
            policy_version=POLICY_VERSION,
            model_version="none",
            episode_set=episode_tags.get("episode_set"),
            episode_id=episode_tags.get("episode_id"),
            scenario_id=episode_tags.get("scenario_id"),
            note=episode_tags.get("note"),
        )
        self.run_context.write_config_snapshot(self._build_config_snapshot())
        recovery = self.run_context.find_incomplete_previous_run()
        if recovery:
            self.run_context.log_event("RECOVERY_NOTE", {"previous_run": recovery})
        self.run_context.log_event(
            "RUN_START", {"mode": self.config.mode, "episode": episode_tags}
        )
        if any(episode_tags.values()):
            self.run_context.log_event("SESSION_MARK", {"episode": episode_tags})
        run_start_ts = time.time()
        self.stats = StatsAggregator(start_ts=run_start_ts)
        self.action_ledger = ActionLedger(
            self.run_context.run_dir / "action_ledger.jsonl", self.run_context
        )
        current_regime = self._get_strategy_mode()
        if current_regime:
            self.stats.on_regime_change(current_regime, now_ts=run_start_ts)
        self._stop_requested = False
        self._stop_reason = None

        def _request_stop(signum: int, _frame) -> None:
            self._stop_requested = True
            self._stop_reason = f"signal_{signum}"

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, _request_stop)
            except Exception:
                continue
        if self.api_server:
            self.api_server.start()
        if self.lockbot_policy_runner:
            self.lockbot_policy_runner.start()
        try:
            while not self._stop_requested:
                # Check ZMQ Heartbeats
                if hasattr(self, "heartbeat_subscriber"):
                    hb_payload = self.heartbeat_subscriber.check_messages()
                    if hb_payload:
                        # Normalize payload fields
                        if "source" in hb_payload:
                            hb_payload["service_id"] = hb_payload["source"]
                        if "status" in hb_payload:
                            hb_payload["state"] = hb_payload["status"]

                        valid_keys = HeartbeatPayload.__dataclass_fields__.keys()
                        filtered = {
                            k: v for k, v in hb_payload.items() if k in valid_keys
                        }
                        self.update_heartbeat(HeartbeatPayload(**filtered))

                self.process_manager.tick()
                self.telemetry.update_process_state(
                    self.process_manager.get_status_payload()
                )

                # --- RISK & POLICY LOGIC (Stage 3) ---
                # 1. Dead Bot Check
                hb_state = self.heartbeat_server.get_state()
                if hb_state.last_heartbeat_time:
                    elapsed = (
                        datetime.now(timezone.utc) - hb_state.last_heartbeat_time
                    ).total_seconds()
                    if elapsed > 5.0:
                        self.logger.warning(
                            f"Bot Dead? No heartbeat for {elapsed:.1f}s"
                        )
                        # Mark bot_status = DEAD (logic only, process might be running)

                # 2. Risk Check & Policy Publish
                risk_state = self.risk_engine.get_state()
                allow_trading = True
                reason = "OK"

                if (
                    risk_state.equity_start is not None
                    and risk_state.equity_now is not None
                ):
                    daily_loss = risk_state.equity_start - risk_state.equity_now
                    # Use max_daily_loss from config (assuming absolute value or handle pct)
                    limit = self.risk_config.max_daily_loss_abs
                    if limit and daily_loss > limit:
                        allow_trading = False
                        reason = "RISK_LIMIT"

                policy_payload = {
                    "allow_trading": allow_trading,
                    "reason": reason,
                    "timestamp": time.time(),
                }
                self.zmq_policy_publisher.publish(policy_payload)
                # -------------------------------------

                if time.time() >= next_policy_publish_at:
                    self._publish_policy()
                    next_policy_publish_at = (
                        time.time() + self.policy_publish_interval_s
                    )
                if (
                    self.llm_config.enabled
                    and time.time() >= next_llm_check_at
                    and not self.risk_engine.state.halted
                ):
                    try:
                        self.run_llm_check_once()
                    except Exception as exc:
                        self.logger.error("LLM check failed: %s", exc)
                    next_llm_check_at = (
                        time.time() + self.llm_config.check_interval_minutes * 60
                    )
                if snapshot_interval and time.time() >= next_snapshot_at:
                    try:
                        self.run_snapshot_once(verbose=False)
                    except Exception as exc:
                        self.logger.error("Snapshot generation failed: %s", exc)
                    next_snapshot_at = time.time() + snapshot_interval
                if time.time() >= next_stats_at:
                    if self.stats and self.run_context:
                        telemetry_summary = self.telemetry.summary()
                        guard_context = self._build_guard_context(telemetry_summary)
                        guard_result = self.guard_evaluator.evaluate(guard_context)
                        self.run_context.log_event(
                            "GUARD_EVALUATION", guard_result.to_dict()
                        )
                        if not guard_result.allowed:
                            for reason in guard_result.reason_codes:
                                self._record_block(
                                    reason, {"details": guard_result.details}
                                )

                        signals = self._build_regime_signals(telemetry_summary)
                        decision = self.regime_sm.evaluate(
                            signals, guard_result.critical
                        )
                        if decision.changed:
                            self.stats.on_regime_change(decision.current_state)
                            self.run_context.log_event(
                                "REGIME_CHANGE",
                                {
                                    "state": decision.current_state,
                                    "reasons": decision.reason_codes,
                                    "scores": decision.scores,
                                },
                            )
                        elif (
                            decision.proposed_state
                            and decision.blocked_reason
                            and self.action_ledger
                        ):
                            self.action_ledger.append(
                                "ACTION_REJECTED",
                                action_type="SET_REGIME",
                                target="Supervisor",
                                payload={
                                    "proposed_state": decision.proposed_state,
                                    "blocked_reason": decision.blocked_reason,
                                },
                                reason_codes=decision.reason_codes,
                                status="REJECTED",
                            )
                            self.stats.on_action("REJECTED")

                        snapshot = self.stats.snapshot()
                        snapshot["strategy_mode"] = decision.current_state
                        snapshot["guard_allowed"] = guard_result.allowed
                        self.run_context.log_event("STAT_SNAPSHOT", snapshot)
                    next_stats_at = time.time() + stats_interval
                if time.time() >= next_directives_at:
                    if self.run_context and self.stats:
                        telemetry_summary = self.telemetry.summary()
                        guard_context = self._build_guard_context(telemetry_summary)
                        guard_result = self.guard_evaluator.evaluate(guard_context)
                        signals = self._build_regime_signals(telemetry_summary)
                        decision = self.regime_sm.evaluate(
                            signals, guard_result.critical
                        )
                        directives = self._build_directives(
                            decision.current_state, guard_result, episode_tags
                        )
                        if self._update_directives(directives):
                            self.run_context.log_event(
                                "DIRECTIVES_UPDATED", {"regime": decision.current_state}
                            )
                    next_directives_at = time.time() + directives_interval
                if time.time() >= next_autopilot_at:
                    try:
                        autopilot_status = self.autopilot.tick()
                        if self.run_context:
                            self.run_context.log_event(
                                "AUTOPILOT_STATUS",
                                {
                                    "state": autopilot_status.get("state"),
                                    "target_state": autopilot_status.get(
                                        "target_state"
                                    ),
                                    "issues": autopilot_status.get("issues"),
                                },
                            )
                    except Exception as exc:
                        self.logger.error("Autopilot tick failed: %s", exc)
                    next_autopilot_at = time.time() + autopilot_interval
                if time.time() >= next_alerts_at:
                    try:
                        self._evaluate_alerts()
                    except Exception as exc:
                        self.logger.error("Alerts evaluation failed: %s", exc)
                    next_alerts_at = time.time() + alerts_interval
                time.sleep(2.0)
        except KeyboardInterrupt:
            self._stop_requested = True
            self._stop_reason = "keyboard_interrupt"
            self.logger.info("Received interrupt; stopping.")
        except Exception:
            if self.run_context:
                self.run_context.log_error(sys.exc_info()[1] or Exception("unknown"))
            if self.stats:
                self.stats.on_error()
            self.logger.exception("Supervisor loop crashed")
            raise
        finally:
            if self.run_context:
                duration = int(time.time() - run_start_ts)
                end_payload = {"duration_s": duration, "stop_reason": self._stop_reason}
                self.run_context.log_event("RUN_END", end_payload)
                summary = self._build_summary(run_start_ts)
                if self.stats:
                    if self.stats.pnl_realized_total is None:
                        self.stats.pnl_realized_total = summary.get("pnl_total")
                    summary.update(self.stats.finalize())
                self.run_context.write_summary(summary)
                self.run_context.write_artifacts_manifest()
            if self.api_server:
                self.api_server.stop()
            if self.lockbot_policy_runner:
                self.lockbot_policy_runner.stop()
            if hasattr(self, "heartbeat_subscriber"):
                self.heartbeat_subscriber.close()
            if hasattr(self, "zmq_policy_publisher"):
                self.zmq_policy_publisher.close()
            self.process_manager.stop_all()

    def risk_status(self) -> None:
        """Print detailed risk engine state."""

        snapshot = self.risk_engine.get_state()
        print("Risk status")
        print("==========")
        print(f"Trading day: {snapshot.trading_day.isoformat()}")
        print(f"Status: {'HALTED' if snapshot.halted else 'ACTIVE'}")
        if snapshot.halt_reason:
            print(f"Reason: {snapshot.halt_reason}")
        print(f"Equity start: {snapshot.equity_start}")
        print(f"Equity now: {snapshot.equity_now}")
        print(f"Realized PnL today: {snapshot.realized_pnl_today}")
        print(f"Max equity intraday: {snapshot.max_equity_intraday}")
        print(f"Min equity intraday: {snapshot.min_equity_intraday}")
        print(
            f"Limits: daily_loss_abs={self.risk_config.max_daily_loss_abs}, "
            f"daily_loss_pct={self.risk_config.max_daily_loss_pct}, "
            f"drawdown_abs={self.risk_config.max_drawdown_abs}, "
            f"drawdown_pct={self.risk_config.max_drawdown_pct}, "
            f"max_notional_per_symbol={self.risk_config.max_notional_per_symbol}, "
            f"max_leverage={self.risk_config.max_leverage}"
        )

    def _print_status(self, running: bool, info: Optional[ProcessInfo]) -> None:
        heartbeat_state = self.heartbeat_server.get_state()
        heartbeat_status = heartbeat_state.status
        risk_state = self.risk_engine.get_state()
        state = self.process_manager.get_state()

        print("Supervisor status")
        print("=================")
        if running and info:
            uptime = (
                (datetime.now(info.start_time.tzinfo) - info.start_time).total_seconds()
                if info.start_time
                else None
            )
            uptime_str = f"{uptime:.0f}s" if uptime is not None else "unknown"
            print(f"Bot: {state} (pid={info.pid}, uptime={uptime_str})")
        elif info:
            exit_code = (
                info.last_exit_code if info.last_exit_code is not None else "unknown"
            )
            exit_time = (
                info.last_exit_time.isoformat()
                if info.last_exit_time
                else "unknown time"
            )
            print(f"Bot: {state} (last exit code={exit_code}, last exit={exit_time})")
        else:
            print(f"Bot: {state}")

        print(f"Heartbeat: {heartbeat_status}")
        if heartbeat_state.last_heartbeat_time:
            print(f"  last seen at {heartbeat_state.last_heartbeat_time.isoformat()}")
        if heartbeat_state.last_payload:
            print(f"  payload: {heartbeat_state.last_payload}")

        risk_status = "HALTED" if risk_state.halted else "ACTIVE"
        print(f"Risk: {risk_status}")
        if risk_state.halted and risk_state.halt_reason:
            print(f"  reason: {risk_state.halt_reason}")
        equity_now = risk_state.equity_now
        equity_start = risk_state.equity_start
        if equity_now is not None:
            print(f"  equity_now: {equity_now:.2f} {self.risk_config.currency}")
        if equity_start is not None:
            print(f"  equity_start: {equity_start:.2f} {self.risk_config.currency}")
        if equity_now is not None and equity_start is not None:
            daily_loss = equity_start - equity_now
            daily_loss_pct = (daily_loss / equity_start) if equity_start > 0 else None
            print(f"  daily_loss: {daily_loss:.2f} {self.risk_config.currency}")
            if daily_loss_pct is not None:
                print(f"  daily_loss_pct: {daily_loss_pct:.2%}")
        if risk_state.realized_pnl_today is not None:
            print(
                f"  realized_pnl_today: {risk_state.realized_pnl_today:.2f} {self.risk_config.currency}"
            )

        print("  limits:")
        print(
            f"    max_daily_loss_abs: {self.risk_config.max_daily_loss_abs} {self.risk_config.currency}"
        )
        if self.risk_config.max_daily_loss_pct is not None:
            print(f"    max_daily_loss_pct: {self.risk_config.max_daily_loss_pct:.2%}")
        if self.risk_config.max_drawdown_abs is not None:
            print(
                f"    max_drawdown_abs: {self.risk_config.max_drawdown_abs} {self.risk_config.currency}"
            )
        if self.risk_config.max_drawdown_pct is not None:
            print(f"    max_drawdown_pct: {self.risk_config.max_drawdown_pct:.2%}")
        print(
            f"    max_notional_per_symbol: {self.risk_config.max_notional_per_symbol}"
        )
        print(f"    max_leverage: {self.risk_config.max_leverage}")

    def update_heartbeat(self, payload: HeartbeatPayload) -> None:
        """Update heartbeat and propagate to risk engine."""

        self.heartbeat_server.update_heartbeat(payload.__dict__)
        self.risk_engine.update_from_heartbeat(payload)
        self.risk_engine.persist(self.state_dir)

    def evaluate_order(self, order: OrderRequest) -> RiskDecision:
        """Expose risk evaluation for future integrations."""

        decision = self.risk_engine.evaluate_order(order)
        if not decision.allowed:
            self.logger.warning(
                "Order blocked: %s - %s", decision.code, decision.reason
            )
            self._record_block(
                decision.code, {"symbol": order.symbol, "reason": decision.reason}
            )
        return decision

    def audit(self, target_date: date) -> None:
        """Generate audit report for a given date."""

        events = load_events_for_date(self.paths.events_dir, target_date)
        if not events:
            print(
                f"No events found for {target_date.isoformat()} in {self.paths.events_dir}"
            )
            return

        stats = compute_stats(events)
        report = render_markdown_report(stats, self.risk_config)

        reports_dir = self.paths.reports_dir
        reports_dir.mkdir(parents=True, exist_ok=True)
        report_path = reports_dir / f"audit_{target_date.isoformat()}.md"
        report_path.write_text(report, encoding="utf-8")

        print(f"Audit for {target_date.isoformat()}")
        print(f"- Total decisions: {stats.total_order_decisions}")
        print(f"- Allowed: {stats.allowed_orders} | Denied: {stats.denied_orders}")
        if stats.denied_by_code:
            print("- Deny codes:")
            for code, count in sorted(stats.denied_by_code.items()):
                print(f"  - {code}: {count}")
        print(f"- Halt events: {stats.halt_events}")
        print(f"- Bot starts: {stats.bot_starts}, stops: {stats.bot_stops}")
        print(f"- Anomalies: {stats.anomalies}")
        print(f"Markdown report written to: {report_path}")

    def run_llm_check_once(self) -> None:
        """Run a single LLM risk moderation check."""

        print(f"[SUP] DEBUG: run_llm_check_once evaluating... enabled={self.llm_config.enabled}, dry_run={self.llm_config.dry_run}")
        if not self.llm_config.enabled:
            print("[SUP] LLM supervisor is disabled.")
            return

        snapshot = state_utils.load_risk_state(self.state_dir, today=date.today())
        self.risk_engine.state = snapshot
        advice = self.llm_supervisor.run_check(
            date.today(), snapshot, mode=self.config.mode
        )
        if advice is None:
            print(
                "[SUP] LLM check produced no advice (disabled, insufficient data, or error)."
            )
            return

        print(
            f"[SUP] 🚀 LLM Advice: action={advice.action.value}, risk_multiplier={advice.risk_multiplier}, comment={advice.comment}"
        )

        # FOR UAT: Ignore dry_run and forcibly apply the LLM advice
        # if self.llm_config.dry_run:
        #     self.logger.info("LLM advice received (dry-run): %s", advice)
        #     return

        print(f"[SUP] Applying LLM Advice: {advice}")
        self.risk_engine.apply_llm_advice(advice)
        self.risk_engine.persist(self.state_dir)

    def ingest_trade_result(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        data = dict(payload)
        if self.run_context:
            self.run_context.log_event("TRADE_RESULT", data)
        if self.stats:
            self.stats.on_trade_result(data)
        return {"status": "ok"}

    def _record_block(
        self, reason_code: str, details: Optional[Dict[str, Any]] = None
    ) -> None:
        if self.stats:
            self.stats.on_block(reason_code, details)
        if self.run_context:
            payload = {"reason_code": reason_code}
            if details:
                payload.update(details)
            self.run_context.log_event("BLOCK_REASON", payload)

    def _build_guard_context(
        self, telemetry_summary: Dict[str, object]
    ) -> Dict[str, Optional[float]]:
        drawdown_pct = None
        risk_state = self.risk_engine.get_state()
        if risk_state.equity_start and risk_state.equity_now is not None:
            try:
                drawdown_pct = (risk_state.equity_start - risk_state.equity_now) / max(
                    risk_state.equity_start, 1e-9
                )
            except Exception:
                drawdown_pct = None
        return {
            "spread_bps": _coerce_float(telemetry_summary.get("spread_bps")),
            "depth_usd": _coerce_float(telemetry_summary.get("depth_usd")),
            "margin_used_pct": _coerce_float(telemetry_summary.get("margin_used_pct")),
            "liq_distance_pct": _coerce_float(
                telemetry_summary.get("liq_distance_pct")
            ),
            "drawdown_pct": drawdown_pct,
            "loss_streak": _coerce_float(telemetry_summary.get("loss_streak")),
            "trades_per_hour": _coerce_float(telemetry_summary.get("trades_1h")),
        }

    def _build_regime_signals(
        self, telemetry_summary: Dict[str, object]
    ) -> Dict[str, Optional[float]]:
        trend_score = _coerce_float(telemetry_summary.get("trend_score"))
        volatility = _coerce_float(telemetry_summary.get("volatility"))
        spread_bps = _coerce_float(telemetry_summary.get("spread_bps"))
        return {
            "trend_score": trend_score,
            "volatility": volatility,
            "spread_bps": spread_bps,
        }

    def _build_directives(
        self, regime_state: str, guard_result: GuardResult, episode_tags: Dict[str, Any]
    ) -> Dict[str, Any]:
        allow_scalp = guard_result.allowed and regime_state in {"RANGE", "TREND"}
        directives = {
            "ts_utc": datetime.now(timezone.utc).isoformat(),
            "run_id": self.run_context.run_id if self.run_context else None,
            "regime": regime_state,
            "allow": {
                "scalp_enter": bool(allow_scalp),
                "scalp_increase": bool(allow_scalp),
                "lock_freeze": regime_state in {"PANIC", "FREEZE"},
                "lock_unwind": regime_state == "UNWIND",
            },
            "blocked_reasons": list(guard_result.reason_codes),
            "guard_summary": guard_result.to_dict(),
            "episode_tags": episode_tags,
        }
        return directives

    def _update_directives(self, directives: Dict[str, Any]) -> bool:
        if not self.run_context:
            return False
        payload = {k: directives.get(k) for k in sorted(directives)}
        payload_json = json.dumps(payload, sort_keys=True, default=str)
        new_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        if new_hash == self._directives_last_hash:
            return False
        self._directives_last_hash = new_hash
        if self.action_ledger:
            action_id = self.action_ledger.append(
                "ACTION_PROPOSED",
                action_type="UPDATE_DIRECTIVES",
                target="AllBots",
                payload=directives,
                reason_codes=directives.get("blocked_reasons", []),
                status="PROPOSED",
            )
            if self.stats:
                self.stats.on_action("PROPOSED")
            self.action_ledger.append(
                "ACTION_APPLIED",
                action_type="UPDATE_DIRECTIVES",
                target="AllBots",
                payload=directives,
                reason_codes=directives.get("blocked_reasons", []),
                action_id=action_id,
                status="APPLIED",
            )
            if self.stats:
                self.stats.on_action("APPLIED")
        self.run_context.update_directives(directives, self.project_root / "runtime")
        return True

    def _build_config_snapshot(self) -> Dict[str, Any]:
        return {
            "paths": self.paths,
            "supervisor": self.config,
            "risk": self.risk_config,
            "llm_supervisor": self.llm_config,
            "trend_evaluator": self.trend_config,
            "market_risk_monitor": self.market_risk_config,
            "trading_behavior": self.behavior_config,
            "snapshot_scheduler": self.snapshot_config,
            "meta_supervisor": self.meta_config,
            "dashboard": self.dashboard_config,
            "tsdb": self.tsdb_config,
            "tsdb_retention": self.tsdb_retention,
            "control_policy": {
                "regime_sm": self.regime_cfg,
                "guards": self.guard_cfg,
                "directives": self.directives_cfg,
            },
            "project_root": self.project_root,
        }

    def _build_summary(self, start_ts: float) -> Dict[str, Any]:
        now = time.time()
        risk_state = self.risk_engine.get_state()
        max_drawdown = None
        if (
            risk_state.equity_start is not None
            and risk_state.min_equity_intraday is not None
        ):
            max_drawdown = risk_state.equity_start - risk_state.min_equity_intraday
        return {
            "start_ts_utc": datetime.fromtimestamp(
                start_ts, tz=timezone.utc
            ).isoformat(),
            "end_ts_utc": datetime.now(timezone.utc).isoformat(),
            "duration_s": int(now - start_ts),
            "pnl_total": risk_state.realized_pnl_today,
            "wins": None,  # TODO(stage1): wire trade outcomes into summary
            "losses": None,  # TODO(stage1): wire trade outcomes into summary
            "trades": None,  # TODO(stage1): wire trade count into summary
            "max_drawdown": max_drawdown,
            "max_margin_used": None,  # TODO(stage1): add margin tracking
            "min_liq_distance": None,  # TODO(stage1): add liquidation distance tracking
            "fees_paid": None,  # TODO(stage1): add fee tracking
            "funding_paid": None,  # TODO(stage1): add funding tracking
            "regime_time_share": {},  # TODO(stage1): add regime tracking
            "blocked_actions_count": 0,  # TODO(stage1): count risk blocks
            "errors_count": self.run_context.errors_count if self.run_context else 0,
        }

    def _get_strategy_mode(self) -> Optional[str]:
        try:
            overview = self.dashboard_overview()
            mode = overview.get("strategy_mode")
            return str(mode) if mode else None
        except Exception:
            return None

    def run_snapshot_once(self, verbose: bool = True) -> Optional[SnapshotReport]:
        """Generate a supervisor snapshot immediately."""

        snapshot = self.snapshot_scheduler.run_once()
        if verbose:
            if snapshot:
                print(
                    f"Snapshot @ {snapshot.timestamp.isoformat()} trend={snapshot.trend} "
                    f"risk={snapshot.market_risk_level} pnl={snapshot.behavior_pnl_quality}"
                )
            else:
                print("Snapshot not generated (disabled or insufficient data).")
        return snapshot

    def handle_heartbeat(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        """Handle heartbeat payload from API."""

        with self._lock:
            self.heartbeat_server.update_heartbeat(payload)
            state = self.heartbeat_server.get_state()
            if state.last_payload:
                self.risk_engine.update_from_heartbeat(state.last_payload)
                self.risk_engine.persist(self.state_dir)
            snapshot = self.risk_engine.get_state()
        return {
            "heartbeat_status": state.status,
            "last_heartbeat_time": (
                state.last_heartbeat_time.isoformat()
                if state.last_heartbeat_time
                else None
            ),
            "risk": {
                "halted": snapshot.halted,
                "halt_reason": snapshot.halt_reason,
                "llm_paused": snapshot.llm_paused,
                "llm_risk_multiplier": snapshot.llm_risk_multiplier,
            },
        }

    def evaluate_order_from_json(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        """Convert JSON payload to OrderRequest and evaluate."""

        try:
            side = payload["side"].upper()
            order_type = payload["order_type"].upper()
            symbol = str(payload["symbol"])
            quantity = float(payload["quantity"])
        except KeyError as exc:
            raise ValueError(f"Missing field: {exc}") from exc
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid numeric field: {exc}") from exc

        try:
            order_request = OrderRequest(
                symbol=symbol,
                side=OrderSide(side),
                order_type=OrderType(order_type),
                quantity=quantity,
                price=(
                    float(payload["price"])
                    if payload.get("price") is not None
                    else None
                ),
                notional=(
                    float(payload["notional"])
                    if payload.get("notional") is not None
                    else None
                ),
                leverage=(
                    float(payload["leverage"])
                    if payload.get("leverage") is not None
                    else None
                ),
                is_reduce_only=bool(payload.get("is_reduce_only", False)),
            )
        except ValueError as exc:
            raise ValueError(f"Invalid enum value: {exc}") from exc

        with self._lock:
            decision = self.risk_engine.evaluate_order(order_request)
            snapshot = self.risk_engine.get_state()
            self.risk_engine.persist(self.state_dir)
            if not decision.allowed:
                self._record_block(
                    decision.code,
                    {"symbol": order_request.symbol, "reason": decision.reason},
                )

        return {
            "allowed": decision.allowed,
            "code": decision.code,
            "reason": decision.reason,
            "risk": {
                "halted": snapshot.halted,
                "halt_reason": snapshot.halt_reason,
                "llm_paused": snapshot.llm_paused,
                "llm_risk_multiplier": snapshot.llm_risk_multiplier,
            },
        }

    def get_status_snapshot(self) -> Dict[str, Any]:
        """Return a compact status snapshot."""

        running = self.process_manager.is_running()
        info = self.process_manager.get_info()
        status = self.process_manager.get_status_payload()
        heartbeat_state = self.heartbeat_server.get_state()
        snapshot = self.risk_engine.get_state()

        bot_data: Dict[str, Any] = {
            "running": running,
            "state": status.get("state"),
            "restarts": status.get("restarts"),
            "last_exit_code": status.get("last_exit_code"),
        }
        if running and info:
            uptime = (
                (datetime.now(info.start_time.tzinfo) - info.start_time).total_seconds()
                if info.start_time
                else None
            )
            bot_data.update({"pid": info.pid, "uptime_seconds": uptime})
        elif info:
            bot_data.update(
                {
                    "last_exit_code": info.last_exit_code,
                    "last_exit_time": (
                        info.last_exit_time.isoformat() if info.last_exit_time else None
                    ),
                }
            )

        return {
            "bot": bot_data,
            "heartbeat": {
                "status": heartbeat_state.status,
                "last_heartbeat_time": (
                    heartbeat_state.last_heartbeat_time.isoformat()
                    if heartbeat_state.last_heartbeat_time
                    else None
                ),
            },
            "risk": {
                "halted": snapshot.halted,
                "halt_reason": snapshot.halt_reason,
                "llm_paused": snapshot.llm_paused,
                "llm_risk_multiplier": snapshot.llm_risk_multiplier,
            },
        }

    def get_latest_snapshot_payload(self) -> Dict[str, Any]:
        """Expose the latest supervisor snapshot for API consumers."""

        snapshot = self.snapshot_scheduler.latest_snapshot
        if not snapshot:
            return {
                "timestamp": None,
                "trend": "UNKNOWN",
                "trend_confidence": 0.0,
                "market_risk_level": "LOW",
                "market_risk_triggers": [],
                "behavior_pnl_quality": "UNKNOWN",
                "behavior_signal_quality": "UNKNOWN",
                "behavior_flags": [],
                "total_trades": 0,
                "recent_winrate": 0.0,
                "recent_drawdown_pct": 0.0,
            }
        return snapshot.to_dict()

    # Dashboard facades
    def dashboard_overview(self) -> Dict[str, Any]:
        return self.dashboard_store.overview()

    def dashboard_health(self) -> Dict[str, Any]:
        if not self.dashboard_service or not self.dashboard_service.enabled:
            return {"status": "disabled"}
        health = self.dashboard_service.get_health()
        return {
            "status": health.status,
            "issues": health.issues,
            "last_heartbeat_at": (
                health.last_heartbeat_at.isoformat()
                if health.last_heartbeat_at
                else None
            ),
            "last_snapshot_at": (
                health.last_snapshot_at.isoformat() if health.last_snapshot_at else None
            ),
        }

    def dashboard_events(
        self, limit: Optional[int] = None, types: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        if not self.dashboard_service or not self.dashboard_service.enabled:
            return []
        evs = self.dashboard_service.list_events(limit=limit, types=types)
        return [
            {
                "timestamp": ev.timestamp.isoformat(),
                "event_type": ev.event_type,
                "symbol": ev.symbol,
                "details": ev.details,
            }
            for ev in evs
        ]

    def dashboard_summary(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        summary = self._build_alert_summary()
        summary["ts"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        summary["symbol"] = symbol or self.autopilot_cfg.policy_symbol
        summary["autopilot"] = self.autopilot.status()
        summary["bot"] = self.get_bot_status()
        summary["policy_current"] = self.autopilot.policy_manager.current_record()
        summary["tsdb"] = self.get_tsdb_status()
        summary["kill_switch"] = self._read_kill_switch_state()
        return summary

    def dashboard_strategies(self) -> Dict[str, Any]:
        return {"strategies": self.dashboard_store.strategies()}

    def dashboard_performance(self) -> Dict[str, Any]:
        return self.dashboard_store.performance()

    def dashboard_alerts(self) -> Dict[str, Any]:
        payload = self.alerts_snapshot()
        payload["ts_ms"] = int(time.time() * 1000)
        return payload

    def dashboard_audit(
        self, since_ts_ms: Optional[int] = None, limit: int = 200
    ) -> Dict[str, Any]:
        return self.dashboard_store.audit(since_ts_ms, limit)

    def dashboard_reset_counters(self) -> Dict[str, Any]:
        return self.dashboard_store.reset_counters()

    def lockbot_send_cmd(self, cmd: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self.lockbot_client:
            raise RuntimeError("lockbot_control_disabled")
        cmd_id = self.lockbot_client.send_command(cmd, payload)
        return {"status": "sent", "cmd_id": cmd_id}

    def lockbot_status(self) -> Dict[str, Any]:
        if not self.lockbot_client:
            return {"status": "disabled"}
        status = self.lockbot_client.status()
        return {"status": "ok", "payload": status}

    def lockbot_execution_arm(
        self, mode: str, ttl_s: int, reason: str = ""
    ) -> Dict[str, Any]:
        if not self.lockbot_client:
            return {"status": "disabled"}
        payload = {"mode": mode, "ttl_s": ttl_s, "reason": reason}
        cmd_id = self.lockbot_client.send_command("ARM_EXECUTION", payload)
        self.dashboard_audit_logger.append(
            severity="INFO",
            component="lockbot_exec",
            strategy_id="LockBotBTC",
            symbol=self.lockbot_cfg.symbol,
            event_type="EXEC_ARM",
            payload=payload,
            correlation_id=cmd_id,
        )
        return {"status": "sent", "cmd_id": cmd_id}

    def lockbot_execution_disarm(self, reason: str = "") -> Dict[str, Any]:
        if not self.lockbot_client:
            return {"status": "disabled"}
        payload = {"reason": reason}
        cmd_id = self.lockbot_client.send_command("DISARM_EXECUTION", payload)
        self.dashboard_audit_logger.append(
            severity="INFO",
            component="lockbot_exec",
            strategy_id="LockBotBTC",
            symbol=self.lockbot_cfg.symbol,
            event_type="EXEC_DISARM",
            payload=payload,
            correlation_id=cmd_id,
        )
        return {"status": "sent", "cmd_id": cmd_id}

    def lockbot_execution_cancel_all(
        self, scope: str = "OPEN_ONLY", reason: str = ""
    ) -> Dict[str, Any]:
        if not self.lockbot_client:
            return {"status": "disabled"}
        payload = {"scope": scope, "reason": reason}
        cmd_id = self.lockbot_client.send_command("CANCEL_ALL", payload)
        self.dashboard_audit_logger.append(
            severity="INFO",
            component="lockbot_exec",
            strategy_id="LockBotBTC",
            symbol=self.lockbot_cfg.symbol,
            event_type="EXEC_CANCEL_ALL",
            payload=payload,
            correlation_id=cmd_id,
        )
        return {"status": "sent", "cmd_id": cmd_id}

    def lockbot_execution_status(self, limit: int = 20) -> Dict[str, Any]:
        if not self.lockbot_client:
            return {"status": "disabled", "events": []}
        status = self.lockbot_client.status()
        payload = status.get("payload") if isinstance(status, dict) else None
        exec_state = payload.get("execution") if isinstance(payload, dict) else None
        return {
            "status": "ok",
            "execution": exec_state,
            "events": self.lockbot_client.exec_recent(limit),
        }

    def lockbot_policy_status(self) -> Dict[str, Any]:
        if not self.lockbot_policy_runner:
            return {"status": "disabled"}
        return {"status": "ok", "payload": self.lockbot_policy_runner.status()}

    def lockbot_policy_set_enabled(self, enabled: bool) -> Dict[str, Any]:
        if not self.lockbot_policy_runner:
            return {"status": "disabled"}
        self.lockbot_policy_runner.set_enabled(enabled)
        return {"status": "ok", "enabled": bool(enabled)}

    def lockbot_policy_decisions(self, limit: int = 20) -> Dict[str, Any]:
        if not self.lockbot_policy_runner:
            return {"status": "disabled", "decisions": []}
        return {
            "status": "ok",
            "decisions": self.lockbot_policy_runner.decisions(limit),
        }

    def _build_alert_summary(self) -> Dict[str, Any]:
        metrics = self.autopilot.collector.collect()
        telemetry_summary = self.telemetry.summary()
        ingest_status = self.get_tsdb_status().get("ingest", {})
        latency_p95 = _coerce_float(metrics.raw.get("latency_p95_ms")) or _coerce_float(
            telemetry_summary.get("latency_ms_p95")
        )
        summary = {
            "mode": metrics.mode,
            "breaker_active": metrics.breaker_active,
            "breaker_reason": metrics.breaker_reason,
            "tick_age_ms": metrics.tick_age_ms,
            "book_age_ms": metrics.book_age_ms,
            "latency_p95_ms": latency_p95,
            "policy_mismatch": self._policy_mismatch(metrics.raw),
            "policy_mode": metrics.policy_mode,
            "policy_allow_trading": metrics.policy_allow_trading,
            "reject_top": metrics.raw.get("reject_top"),
            "last_error": metrics.last_error,
            "last_error_ts": metrics.last_error_ts,
        }
        return {
            "summary": summary,
            "telemetry": telemetry_summary,
            "ingest": ingest_status,
            "dashboard": self.dashboard_store.alert_summary(),
        }

    def _evaluate_alerts(self) -> None:
        payload = self._build_alert_summary()
        self._last_alert_result = self.alert_engine.evaluate(payload)
        try:
            self.dashboard_store.record_alert_transitions(
                self._last_alert_result, payload
            )
        except Exception as exc:
            self.logger.debug("Dashboard alert audit failed: %s", exc)
        self._last_alert_eval_ts = time.time()

    def _policy_mismatch(self, raw: Dict[str, Any]) -> bool:
        if raw.get("policy_mismatch") or raw.get("schema_mismatch"):
            return True
        reject_candidates: List[Dict[str, Any]] = []
        for key in ("reject_top", "rejects", "reject_counters"):
            value = raw.get(key)
            if isinstance(value, dict):
                reject_candidates.append(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        reject_candidates.append(item)
        for bucket in reject_candidates:
            for reason in bucket.keys():
                reason_str = str(reason).upper()
                if "SCHEMA" in reason_str or "POLICY" in reason_str:
                    return True
        return False

    def _read_kill_switch_state(self) -> Dict[str, Any]:
        path = self.paths.quantumedge_root / "state" / "kill_switch.json"
        if not path.exists():
            return {"enabled": False}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"enabled": False}
        enabled = bool(payload.get("enabled", False))
        return {
            "enabled": enabled,
            "reason": payload.get("reason"),
            "ts": payload.get("ts"),
        }

    def get_tsdb_status(self) -> Dict[str, Any]:
        enabled = bool(self.tsdb_writer)
        reachable = None
        if enabled and self.tsdb_backend == "clickhouse":
            import urllib.request

            try:
                with urllib.request.urlopen(
                    f"{self.tsdb_config.clickhouse_url}/ping", timeout=3
                ) as resp:
                    reachable = resp.status == 200 and resp.read() in (b"Ok.", b"Ok.\n")
            except Exception:
                reachable = False
        elif enabled and self.tsdb_backend == "questdb":
            # QuestDB ILP has no ping; assume reachable if writer exists
            reachable = True
        ingest_status = self._load_ingest_status()
        return {
            "enabled": enabled,
            "backend": self.tsdb_backend,
            "reachable": reachable,
            "last_write_at": (
                self.tsdb_writer.last_write_at.isoformat()
                if enabled and self.tsdb_writer.last_write_at
                else None
            ),
            "queue_depth": self.tsdb_writer.queue_depth if enabled else 0,
            "ingest": ingest_status,
        }

    def get_tsdb_health(self) -> Dict[str, Any]:
        status = self.get_tsdb_status()
        return {
            "backend": status.get("backend"),
            "enabled": status.get("enabled"),
            "reachable": status.get("reachable"),
            "ingest": status.get("ingest"),
        }

    def tsdb_latest_metrics(self, symbol: str) -> Dict[str, Any]:
        symbol = sanitize_symbol(symbol)
        if self.tsdb_config.enabled and self.tsdb_backend == "questdb":
            query_url = self._questdb_query_url()
            if query_url:
                rows = questdb_query(
                    query_url,
                    f"SELECT * FROM qe_metrics WHERE symbol='{symbol}' ORDER BY timestamp DESC LIMIT 1",
                )
                return {"rows": rows}
        fallback = self._fallback_latest_metrics()
        return {"rows": [fallback] if fallback else []}

    def tsdb_recent_events(self, symbol: str, limit: int = 200) -> Dict[str, Any]:
        symbol = sanitize_symbol(symbol)
        limit = min(max(int(limit), 1), 1000)
        if self.tsdb_config.enabled and self.tsdb_backend == "questdb":
            query_url = self._questdb_query_url()
            if query_url:
                rows = questdb_query(
                    query_url,
                    f"SELECT * FROM qe_events WHERE symbol='{symbol}' ORDER BY timestamp DESC LIMIT {limit}",
                )
                return {"rows": rows}
        fallback = self._fallback_recent_events(limit)
        return {"rows": fallback}

    def tsdb_timeseries(
        self, metric: str, symbol: str, start: str, end: str, bucket: str
    ) -> Dict[str, Any]:
        if not self.tsdb_config.enabled or self.tsdb_backend != "questdb":
            return {"error": "tsdb_disabled"}
        if not start or not end:
            return {"error": "missing_range"}
        query_url = self._questdb_query_url()
        if not query_url:
            return {"error": "questdb_query_url_missing"}
        sql = build_timeseries_query(metric, symbol, start, end, bucket)
        rows = questdb_query(query_url, sql)
        return {"rows": rows}

    def tsdb_query_sql(self, sql: str) -> Dict[str, Any]:
        if not self.tsdb_config.enabled or self.tsdb_backend != "questdb":
            return {"error": "tsdb_disabled"}
        query_url = self._questdb_query_url()
        if not query_url:
            return {"error": "questdb_query_url_missing"}
        return {"rows": questdb_query(query_url, sql)}

    def _questdb_query_url(self) -> str:
        if self.tsdb_config.questdb_query_url:
            return self.tsdb_config.questdb_query_url
        return derive_questdb_query_url(self.tsdb_config.questdb_ilp_http_url)

    def _resolve_qe_path(self, value: str) -> Path:
        path = Path(value)
        if not path.is_absolute():
            path = self.paths.qe_root / path
        return path.resolve()

    def _load_ingest_status(self) -> Dict[str, Any]:
        state_path = self._resolve_qe_path(self.tsdb_config.ingest_state_path)
        if not state_path.exists():
            return {
                "enabled": bool(self.tsdb_config.ingest_enabled),
                "state_path": str(state_path),
                "status": "missing",
            }
        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            return {
                "enabled": bool(self.tsdb_config.ingest_enabled),
                "state_path": str(state_path),
                "status": "bad_json",
            }
        now = time.time()
        event_lag = _compute_lag(payload.get("last_event_ts"), now)
        metrics_lag = _compute_lag(payload.get("last_metrics_ts"), now)
        return {
            "enabled": bool(self.tsdb_config.ingest_enabled),
            "state_path": str(state_path),
            "last_event_ts": payload.get("last_event_ts"),
            "last_metrics_ts": payload.get("last_metrics_ts"),
            "event_lag_sec": event_lag,
            "metrics_lag_sec": metrics_lag,
            "malformed_events": payload.get("malformed_events"),
            "dropped_events": payload.get("dropped_events"),
            "last_updated": payload.get("last_updated"),
        }

    def _fallback_latest_metrics(self) -> Optional[Dict[str, Any]]:
        path = self._resolve_qe_path(self.tsdb_config.ingest_metrics_path)
        payload = parse_metrics_file(path)
        return payload

    def _fallback_recent_events(self, limit: int) -> List[Dict[str, Any]]:
        path = self._resolve_qe_path(self.tsdb_config.ingest_events_path)
        if not path.exists():
            return []
        from collections import deque

        buffer = deque(maxlen=limit)
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    payload = parse_event_line(line)
                    if payload:
                        buffer.append(payload)
        except OSError:
            return []
        return list(buffer)

    def run_meta_supervisor_once(self, force: bool = False) -> None:
        """Trigger Meta-Agent supervisor cycle."""

        runner = MetaSupervisorRunner(
            self.meta_config,
            self.paths,
            self.logger,
            self.event_logger,
            self.meta_supervisor_state_path,
        )
        ctx = MetaSupervisorContext(
            now=datetime.now(),
            bot_running=self.process_manager.is_running(),
            last_audit_reports=[],
        )
        state = runner.run_cycle(ctx, force=force)
        status = state.last_status or "UNKNOWN"
        reason = state.last_reason or "n/a"
        reports = ", ".join(state.last_reports) if state.last_reports else "none"
        print(f"Meta-supervisor status: {status} (reason={reason}, reports={reports})")

    def run_diag(self) -> int:
        """Run diagnostics, returning exit code (0=OK, 1=FAIL)."""

        results: List[tuple[str, str]] = []

        def add(status: str, message: str) -> None:
            results.append((status, message))

        run_bot = Path(self.config.bot_entrypoint)
        if not run_bot.is_absolute():
            run_bot = (self.paths.qe_root / run_bot).resolve()
        if run_bot.exists():
            add("OK", f"QuantumEdge path: {run_bot}")
        else:
            add("FAIL", f"QuantumEdge entrypoint missing: {run_bot}")

        if self.paths.logs_dir.exists():
            add("OK", f"Logs dir: {self.paths.logs_dir}")
        else:
            add("FAIL", f"Logs dir missing: {self.paths.logs_dir}")

        if self.trend_config.enabled:
            add(
                "OK",
                f"TrendEvaluator config loaded (window={self.trend_config.history_window_minutes}m)",
            )
        else:
            add("WARN", "TrendEvaluator disabled")

        if self.market_risk_config.enabled:
            add(
                "OK",
                f"MarketRiskMonitor config loaded (history={self.market_risk_config.history_window_minutes}m)",
            )
        else:
            add("WARN", "MarketRiskMonitor disabled")

        if self.behavior_config.enabled:
            add(
                "OK",
                f"TradingBehaviorAnalyzer history trades={self.behavior_config.history_trades}",
            )
        else:
            add("WARN", "TradingBehaviorAnalyzer disabled")

        if self.snapshot_config.enabled:
            add(
                "OK",
                f"Snapshot scheduler configured (interval={self.snapshot_config.interval_minutes}m, window={self.snapshot_config.history_window_minutes}m)",
            )
        else:
            add("WARN", "Snapshot scheduler disabled")

        if self.snapshots_dir.exists():
            add("OK", f"Snapshots dir: {self.snapshots_dir}")
        else:
            add("FAIL", f"Snapshots dir missing: {self.snapshots_dir}")

        if self.snapshot_scheduler.latest_snapshot:
            ts = self.snapshot_scheduler.latest_snapshot.timestamp.isoformat()
            add("OK", f"Latest snapshot at {ts}")
        else:
            add("WARN", "No snapshot generated yet")

        if self.dashboard_service and self.dashboard_service.enabled:
            add("OK", "Dashboard service enabled")
        else:
            add("WARN", "Dashboard service disabled")

        if self.process_specs:
            add("OK", f"Process specs loaded ({len(self.process_specs)} processes)")
        else:
            add("WARN", "No process specs loaded")

        if self.tsdb_writer:
            add("OK", f"TSDB enabled (backend={self.tsdb_backend})")
            status = self.get_tsdb_status()
            if status.get("reachable") is False:
                add("WARN", "TSDB backend unreachable (see /api/v1/tsdb/status)")
        else:
            add("WARN", "TSDB disabled or using noop backend")
        if self.tsdb_retention.enabled:
            add(
                "OK",
                f"TSDB retention config loaded (raw_days={self.tsdb_retention.raw_days})",
            )
        else:
            add("WARN", "TSDB retention disabled")

        for status, message in results:
            print(f"[{status}] {message}")

        fail_count = sum(1 for status, _ in results if status == "FAIL")
        warn_count = sum(1 for status, _ in results if status == "WARN")
        print(f"Summary: {len(results)} checks, {fail_count} FAIL, {warn_count} WARN")
        return 1 if fail_count else 0


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SupervisorAgent CLI")
    parser.add_argument(
        "command",
        choices=[
            "start",
            "stop",
            "restart",
            "status",
            "risk-status",
            "run-foreground",
            "audit",
            "llm-check",
            "meta-supervisor",
            "snapshot",
            "diag",
            "tsdb-status",
            "tsdb-backfill",
            "tsdb-migrate",
            "tsdb-maintain",
            "tsdb-ingest",
            "tsdb-query",
            "report",
            "ml",
            "telemetry",
            "research",
            "episodes-cut",
            "episodes-run",
            "episodes-report",
            "ops-autotune",
            "ops-regression-gate",
            "ops-daily-report",
            "ops-rollback",
            "autopilot-status",
            "autopilot-enable",
            "autopilot-disable",
            "policy-list",
            "policy-rollout",
            "policy-rollback",
            "lockbot-policy-status",
            "lockbot-policy-enable",
            "lockbot-policy-disable",
            "lockbot-policy-decisions",
            "lockbot-exec-arm",
            "lockbot-exec-disarm",
            "lockbot-exec-cancel-all",
            "lockbot-exec-status",
        ],
        help="Command to execute",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON output for diag.",
    )
    parser.add_argument(
        "--date",
        dest="date",
        help="ISO date (YYYY-MM-DD) for audit command; defaults to today.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force action (for meta-supervisor).",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Number of days for tsdb-backfill (overrides config backfill.from_days).",
    )
    parser.add_argument(
        "--from",
        dest="from_ts",
        help="ISO timestamp for tsdb-backfill range start (e.g. 2025-12-01T00:00:00Z).",
    )
    parser.add_argument(
        "--to",
        dest="to_ts",
        help="ISO timestamp for tsdb-backfill range end (e.g. 2025-12-28T00:00:00Z).",
    )
    parser.add_argument(
        "--sql",
        dest="sql",
        help="SQL for tsdb-query.",
    )
    parser.add_argument(
        "--last",
        dest="last",
        help="Report window (e.g. 6h, 24h, 7d).",
    )
    parser.add_argument(
        "--bucket",
        dest="bucket",
        help="Report bucket (e.g. 1m, 5m, 1h).",
    )
    parser.add_argument(
        "--limit",
        dest="limit",
        type=int,
        default=20,
        help="Limit for list commands (e.g. lockbot-policy-decisions).",
    )
    parser.add_argument(
        "--mode",
        dest="exec_mode",
        default="DRY_RUN",
        help="Execution mode for lockbot-exec-arm (DRY_RUN|DEMO_TESTNET|LIVE_MAINNET).",
    )
    parser.add_argument(
        "--ttl-s",
        dest="exec_ttl_s",
        type=int,
        default=300,
        help="Execution arm TTL in seconds.",
    )
    parser.add_argument(
        "--reason",
        dest="exec_reason",
        default="",
        help="Reason for execution arm/disarm/cancel.",
    )
    parser.add_argument(
        "--scope",
        dest="exec_scope",
        default="OPEN_ONLY",
        help="Cancel-all scope (OPEN_ONLY|ALL).",
    )
    parser.add_argument(
        "--config",
        dest="config_path",
        help="Path to supervisor config YAML (defaults to QE_ROOT/config/supervisor.yaml).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply policy changes (ops-autotune).",
    )
    parser.add_argument(
        "--gate-suite",
        dest="gate_suite",
        default="smoke",
        help="Regression gate suite: smoke | core | panic.",
    )
    parser.add_argument(
        "--policy-version",
        dest="policy_version",
        help="Policy version id (vNNN) for ops-regression-gate/ops-rollback.",
    )
    parser.add_argument(
        "--policy-path",
        dest="policy_path",
        help="Explicit policy path for ops-regression-gate.",
    )
    parser.add_argument(
        "--path",
        dest="path",
        help="Policy path for policy-rollout.",
    )
    parser.add_argument(
        "--symbol",
        dest="symbol",
        help="Symbol for policy commands (default from autopilot config).",
    )
    parser.add_argument(
        "--runs-path",
        dest="runs_path",
        help="Override runs directory for ops reports.",
    )
    parser.add_argument(
        "--episode-set", dest="episode_set", help="Episode set tag for this run."
    )
    parser.add_argument(
        "--episode-id", dest="episode_id", help="Episode id tag for this run."
    )
    parser.add_argument(
        "--scenario-id", dest="scenario_id", help="Scenario id tag for this run."
    )
    parser.add_argument("--note", dest="note", help="Optional note for this run.")
    parser.add_argument(
        "ml_args",
        nargs=argparse.REMAINDER,
        help="ModelOps subcommands (e.g. ml train --symbol BTCUSDT --horizons 1,5,30)",
    )
    return parser.parse_args(argv)


def build_app(
    project_root: Path,
    paths_config_path: Path,
    supervisor_config_path: Path,
    supervisor_config_dir: Path,
) -> SupervisorApp:
    paths_config = load_paths_config(paths_config_path)
    runtime_logs_dir = project_root / "runtime" / "logs"
    setup_logging(runtime_logs_dir)
    supervisor_config = load_supervisor_config(supervisor_config_path)
    processes_path = Path(supervisor_config.processes_file)
    if not processes_path.is_absolute():
        processes_path = (project_root / processes_path).resolve()
    if not processes_path.exists():
        fallback = supervisor_config_dir / processes_path.name
        if fallback.exists():
            processes_path = fallback
    process_specs = load_processes_spec(processes_path, paths_config.qe_root)
    risk_config = load_risk_config(supervisor_config_dir / "risk.yaml")
    llm_config = load_llm_supervisor_config(
        supervisor_config_dir / "llm_supervisor.yaml"
    )
    trend_config = load_trend_evaluator_config(
        supervisor_config_dir / "llm_trend_evaluator.yaml"
    )
    market_risk_config = load_market_risk_config(
        supervisor_config_dir / "llm_market_risk.yaml"
    )
    behavior_config = load_trading_behavior_config(
        supervisor_config_dir / "llm_trading_behavior.yaml"
    )
    snapshot_config = load_snapshot_scheduler_config(supervisor_config_path)
    meta_config = load_meta_supervisor_config(
        supervisor_config_dir / "meta_supervisor.yaml", paths_config
    )
    dashboard_config = load_dashboard_config(supervisor_config_dir / "dashboard.yaml")
    lockbot_cfg = load_lockbot_config(supervisor_config_dir / "lockbot.yaml")
    policy_cfg_path = project_root / "configs" / "lockbot_btc_policy.yaml"
    if not policy_cfg_path.exists():
        fallback = supervisor_config_dir / "lockbot_btc_policy.yaml"
        if fallback.exists():
            policy_cfg_path = fallback
    lockbot_policy_cfg = load_lockbot_policy_config(policy_cfg_path)
    tsdb_config = load_tsdb_config(supervisor_config_dir / "tsdb.yaml")
    tsdb_retention = load_tsdb_retention_config(
        supervisor_config_dir / "tsdb_retention.yaml"
    )
    autopilot_cfg = load_autopilot_config(supervisor_config_dir / "autopilot.yaml")
    control_policy_path = resolve_active_policy_path(
        paths_config.runtime_dir, supervisor_config_dir / "policy.yaml"
    )
    regime_cfg = load_regime_config(control_policy_path)
    guard_cfg = load_guard_config(control_policy_path)
    directives_cfg = load_directives_config(control_policy_path)

    # Load services config
    services_path = project_root / "config" / "services.yaml"
    expected_bot_id = "ai_scalper_bot"
    telemetry_port = 5557
    policy_port = 5558

    if services_path.exists():
        try:
            import yaml

            with open(services_path) as f:
                data = yaml.safe_load(f)
                bot_cfg = data.get("services", {}).get("bot", {})
                expected_bot_id = bot_cfg.get("id", expected_bot_id)
                zmq = bot_cfg.get("zmq", {})
                telemetry_port = int(zmq.get("telemetry_port", telemetry_port))
                policy_port = int(zmq.get("policy_port", policy_port))
        except Exception:
            pass

    return SupervisorApp(
        paths_config,
        supervisor_config,
        risk_config,
        llm_config,
        trend_config,
        market_risk_config,
        behavior_config,
        snapshot_config,
        meta_config,
        dashboard_config,
        lockbot_cfg,
        lockbot_policy_cfg,
        tsdb_config,
        tsdb_retention,
        regime_cfg,
        guard_cfg,
        directives_cfg,
        autopilot_cfg,
        process_specs,
        project_root,
        logging.getLogger(__name__),
        telemetry_port=telemetry_port,
        policy_port=policy_port,
        expected_bot_id=expected_bot_id,
    )


def _compute_lag(value: Optional[str], now: float) -> Optional[int]:
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None
    return max(0, int(now - ts))


def _discover_project_root(start: Path) -> Path:
    """Walk up from *start* to find the QuantumEdge project root.

    The project root is identified by containing ``QuantumEdge.py`` **or**
    a ``.git`` directory.  Falls back to *start* if nothing is found.
    """
    current = start.resolve()
    for _ in range(10):  # safety cap
        if (current / "QuantumEdge.py").exists() or (current / ".git").exists():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return start


def main(argv: Optional[list[str]] = None) -> None:
    args = parse_args(argv)
    project_root = Path(__file__).resolve().parent

    qe_paths = None
    if get_qe_paths:
        try:
            qe_paths = get_qe_paths()
        except Exception:
            qe_paths = None

    qe_root = Path(
        os.getenv("QE_ROOT")
        or (qe_paths["qe_root"] if qe_paths else _discover_project_root(project_root))
    )
    os.environ.setdefault("QE_ROOT", str(qe_root))

    config_dir = Path(
        os.getenv("QE_CONFIG_DIR")
        or (qe_paths["config_dir"] if qe_paths else qe_root / "config")
    )
    supervisor_config_dir = Path(
        qe_paths["supervisor_config_dir"] if qe_paths else project_root / "config"
    )
    project_root = Path(qe_paths["supervisor_dir"] if qe_paths else project_root)

    if not supervisor_config_dir.exists():
        supervisor_config_dir = project_root / "config"

    paths_config_path = config_dir / "paths.yaml"
    if not paths_config_path.exists():
        paths_config_path = project_root / "config" / "paths.yaml"

    supervisor_config_path = (
        Path(args.config_path)
        if args.config_path
        else Path(os.getenv("SUPERVISOR_CONFIG") or config_dir / "supervisor.yaml")
    )
    if not supervisor_config_path.exists():
        supervisor_config_path = supervisor_config_dir / "supervisor.yaml"

    try:
        app = build_app(
            project_root,
            paths_config_path,
            supervisor_config_path,
            supervisor_config_dir,
        )
    except Exception as exc:
        print(f"Failed to initialize supervisor: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        if args.command == "start":
            app.start()
        elif args.command == "stop":
            app.stop()
        elif args.command == "restart":
            app.restart()
        elif args.command == "status":
            app.status()
        elif args.command == "risk-status":
            app.risk_status()
        elif args.command == "run-foreground":
            app._episode_tags = {
                "episode_set": args.episode_set,
                "episode_id": args.episode_id,
                "scenario_id": args.scenario_id,
                "note": args.note,
            }
            app.run_foreground()
        elif args.command == "audit":
            target_date = date.today()
            if args.date:
                try:
                    target_date = date.fromisoformat(args.date)
                except ValueError:
                    print("Invalid date format. Use YYYY-MM-DD.", file=sys.stderr)
                    sys.exit(1)
            app.audit(target_date)
        elif args.command == "llm-check":
            app.run_llm_check_once()
        elif args.command == "meta-supervisor":
            app.run_meta_supervisor_once(force=args.force)
        elif args.command == "snapshot":
            app.run_snapshot_once(verbose=True)
        elif args.command == "diag":
            from tools.qe_doctor import run_doctor

            code = run_doctor(json_output=args.json)
            sys.exit(code)
        elif args.command == "tsdb-status":
            print(json.dumps(app.get_tsdb_status(), indent=2))
        elif args.command == "tsdb-migrate":
            from quantum_edge_core.supervisor.supervisor.tsdb.migrations import run_tsdb_migrations

            ok = run_tsdb_migrations(
                project_root,
                app.tsdb_config,
                logging.getLogger(__name__),
                retention=app.tsdb_retention,
            )
            sys.exit(0 if ok else 1)
        elif args.command == "tsdb-backfill":
            if not app.tsdb_config.enabled or app.tsdb_config.backend == "none":
                print("TSDB is disabled; backfill skipped.")
                sys.exit(0)
            if args.from_ts or args.to_ts:
                if not args.from_ts or not args.to_ts:
                    print(
                        "Both --from and --to are required for ranged backfill.",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                from quantum_edge_core.supervisor.supervisor.ingest.backfill import parse_range, run_backfill

                writer = app.tsdb_writer or TsdbWriter(NoopTimeseriesStore())
                writer.start()
                start_ts, end_ts = parse_range(args.from_ts, args.to_ts)
                events_path = app._resolve_qe_path(app.tsdb_config.ingest_events_path)
                exec_path = app._resolve_qe_path(app.tsdb_config.ingest_exec_path)
                run_backfill(
                    events_path,
                    exec_path,
                    start_ts,
                    end_ts,
                    writer,
                    logging.getLogger(__name__),
                    symbol=args.symbol,
                )
                writer.flush()
                sys.exit(0)
            days = args.days or app.tsdb_config.backfill_from_days
            from quantum_edge_core.supervisor.supervisor.tsdb.backfill import run_backfill

            store = app.tsdb_writer.store if app.tsdb_writer else None
            if store is None:
                print("TSDB writer not initialized; cannot backfill.")
                sys.exit(1)
            checkpoint = app.state_dir / "tsdb_backfill_state.json"
            run_backfill(
                app.paths.events_dir,
                store,
                days,
                checkpoint,
                logging.getLogger(__name__),
            )
            print(f"Backfill completed for last {days} day(s).")
        elif args.command == "tsdb-maintain":
            ok = apply_retention_and_rollups(
                project_root,
                app.tsdb_config,
                app.tsdb_retention,
                logging.getLogger(__name__),
            )
            sys.exit(0 if ok else 1)
        elif args.command == "tsdb-ingest":
            action = args.ml_args[0].lower() if args.ml_args else "status"
            state_path = app._resolve_qe_path(app.tsdb_config.ingest_state_path)
            stop_path = state_path.with_suffix(".stop")
            if action == "stop":
                stop_path.write_text(json.dumps({"ts": time.time()}), encoding="utf-8")
                print(
                    json.dumps(
                        {"status": "stopping", "stop_path": str(stop_path)}, indent=2
                    )
                )
                sys.exit(0)
            if action == "status":
                print(json.dumps(app.get_tsdb_status().get("ingest"), indent=2))
                sys.exit(0)
            if not app.tsdb_config.ingest_enabled:
                print(
                    "TSDB ingest disabled in config/tsdb.yaml (ingest.enabled=false)."
                )
                sys.exit(1)
            if stop_path.exists():
                try:
                    stop_path.unlink()
                except OSError:
                    pass
            writer = app.tsdb_writer or TsdbWriter(NoopTimeseriesStore())
            writer.start()
            pipeline = IngestPipeline(
                events_path=app._resolve_qe_path(app.tsdb_config.ingest_events_path),
                metrics_path=app._resolve_qe_path(app.tsdb_config.ingest_metrics_path),
                exec_path=app._resolve_qe_path(app.tsdb_config.ingest_exec_path),
                state_path=state_path,
                writer=writer,
                max_line_kb=app.tsdb_config.ingest_max_line_kb,
                dedupe_cache_size=app.tsdb_config.ingest_dedupe_cache_size,
                logger=logging.getLogger(__name__),
            )
            pipeline.run_forever(app.tsdb_config.ingest_interval_sec, stop_path)
            writer.flush()
            sys.exit(0)
        elif args.command == "tsdb-query":
            if not args.sql:
                print("Missing --sql for tsdb-query.", file=sys.stderr)
                sys.exit(1)
            print(json.dumps(app.tsdb_query_sql(args.sql), indent=2))
        elif args.command == "report":
            if not app.tsdb_config.enabled or app.tsdb_config.backend != "questdb":
                print(
                    "TSDB reports require QuestDB (enable config/tsdb.yaml).",
                    file=sys.stderr,
                )
                sys.exit(1)
            query_url = app._questdb_query_url()
            if not query_url:
                print("QuestDB query URL is not configured.", file=sys.stderr)
                sys.exit(1)
            from tsdb.questdb_client import QuestDbClient
            from reports.tsdb_reports import build_report

            client = QuestDbClient(
                query_url=query_url,
                timeout=3.0,
                max_retries=app.tsdb_config.retry_max_retries,
                base_backoff_s=app.tsdb_config.retry_base_backoff_ms / 1000.0,
                max_backoff_s=app.tsdb_config.retry_max_backoff_ms / 1000.0,
            )
            report = build_report(client, last=args.last, bucket=args.bucket)
            print(json.dumps(report, indent=2))
        elif args.command == "ml":
            from quantum_edge_core.supervisor.mlops.cli import parse_ml_args, run_ml_command

            ml_args = parse_ml_args(args.ml_args)
            code = run_ml_command(ml_args)
            sys.exit(code)
        elif args.command == "telemetry":
            from quantum_edge_core.supervisor.monitoring.cli import (
                parse_telemetry_args,
                run_telemetry_command,
            )

            telemetry_args = parse_telemetry_args(args.ml_args)
            code = run_telemetry_command(app, telemetry_args)
            sys.exit(code)
        elif args.command == "research":
            try:
                from quantum_edge_core.supervisor.research.cli import (
                    parse_research_args,
                    run_research_command,
                )
            except ModuleNotFoundError:
                from research.cli import parse_research_args, run_research_command

            research_args = parse_research_args(args.ml_args)
            code = run_research_command(research_args)
            sys.exit(code)
        elif args.command in {"episodes-cut", "episodes-run", "episodes-report"}:
            from quantum_edge_core.supervisor.supervisor.episodes.cli import (
                parse_episodes_args,
                run_episodes_command,
            )

            episodes_args = parse_episodes_args(args.command, args.ml_args)
            code = run_episodes_command(args.command, episodes_args)
            sys.exit(code)
        elif args.command == "ops-autotune":
            from quantum_edge_core.supervisor.supervisor.policy_store import (
                load_active_policy,
                save_new_policy,
                activate_policy,
            )
            from quantum_edge_core.supervisor.supervisor.ops.autotuner import (
                load_policy_bundle,
                collect_metrics,
                propose_tuning,
            )
            from quantum_edge_core.supervisor.supervisor.ops.config import load_ops_config
            from quantum_edge_core.supervisor.supervisor.ops.regression_gates import run_regression_gates

            runtime_dir = app.paths.runtime_dir
            runs_dir = Path(args.runs_path) if args.runs_path else runtime_dir / "runs"
            ops_cfg = load_ops_config(supervisor_config_dir)
            telemetry_path = None
            if app.config.telemetry_persist_path:
                telemetry_path = Path(app.config.telemetry_persist_path)
                if not telemetry_path.is_absolute():
                    telemetry_path = (app.paths.qe_root / telemetry_path).resolve()
            active_policy, active_version, active_path = load_active_policy(
                runtime_dir, supervisor_config_dir / "policy.yaml"
            )
            policy_bundle = load_policy_bundle(active_policy)
            metrics = collect_metrics(runs_dir, telemetry_path, ops_cfg)

            candidate, changes, notes = propose_tuning(policy_bundle, metrics, ops_cfg)
            ctx, ledger, start_ts = _init_ops_context(
                project_root,
                policy_version=active_version,
                note="ops_autotune",
                config_snapshot={"metrics": metrics, "ops_notes": notes},
            )
            try:
                if not changes:
                    ctx.log_event("ACTION_REJECTED", {"reason": "no_changes"})
                    _finalize_ops_context(ctx, start_ts, {"status": "no_changes"})
                    print(
                        json.dumps(
                            {
                                "status": "no_changes",
                                "metrics": metrics,
                                "notes": notes,
                            },
                            indent=2,
                        )
                    )
                    sys.exit(0)

                if _last_run_has_critical_events(runs_dir):
                    ctx.log_event("ACTION_REJECTED", {"reason": "critical_events"})
                    _finalize_ops_context(
                        ctx,
                        start_ts,
                        {"status": "blocked", "reason": "critical_events"},
                    )
                    print(
                        json.dumps(
                            {"status": "blocked", "reason": "critical_events"}, indent=2
                        )
                    )
                    sys.exit(1)

                version = save_new_policy(
                    candidate,
                    runtime_dir,
                    project_root,
                    reason="autotune",
                    source_run_id=ctx.run_id,
                    previous_policy=active_policy,
                    previous_version_id=active_version,
                )
                ledger.append(
                    "ACTION_PROPOSED",
                    action_type="POLICY_UPDATE",
                    target="Supervisor",
                    payload={
                        "version_id": version.version_id,
                        "changes": changes,
                        "notes": notes,
                    },
                    reason_codes=["AUTOTUNE"],
                    status="PROPOSED",
                )

                gate_result = run_regression_gates(
                    episode_set=args.episode_set or "tick_scenarios_v1",
                    runtime_dir=runtime_dir,
                    candidate_policy_path=version.policy_path,
                    baseline_policy_path=active_path,
                    gate_suite=args.gate_suite,
                )
                ledger.append(
                    "ACTION_RESULT",
                    action_type="REGRESSION_GATE",
                    target="Supervisor",
                    payload=gate_result,
                    reason_codes=["GATE_CHECK"],
                    status="RESULT",
                )
                out_dir = runtime_dir / "regression" / version.version_id
                out_dir.mkdir(parents=True, exist_ok=True)
                (out_dir / "gate_report.json").write_text(
                    json.dumps(gate_result, indent=2), encoding="utf-8"
                )
                (out_dir / "gate_report.md").write_text(
                    _render_gate_report_md(gate_result), encoding="utf-8"
                )

                if args.apply and gate_result.get("passed"):
                    activate_policy(runtime_dir, version.version_id, source="autotune")
                    ledger.append(
                        "ACTION_APPLIED",
                        action_type="POLICY_UPDATE",
                        target="Supervisor",
                        payload={"version_id": version.version_id},
                        reason_codes=["AUTOTUNE"],
                        status="APPLIED",
                    )
                    result = {
                        "status": "applied",
                        "policy_version": version.version_id,
                        "gates": gate_result,
                    }
                    _finalize_ops_context(ctx, start_ts, result)
                    print(json.dumps(result, indent=2))
                    sys.exit(0)

                ledger.append(
                    "ACTION_REJECTED",
                    action_type="POLICY_UPDATE",
                    target="Supervisor",
                    payload={
                        "version_id": version.version_id,
                        "reason": "gates_failed_or_dry_run",
                    },
                    reason_codes=["AUTOTUNE"],
                    status="REJECTED",
                )
                result = {
                    "status": "dry_run" if not args.apply else "gates_failed",
                    "policy_version": version.version_id,
                    "changes": changes,
                    "gates": gate_result,
                }
                _finalize_ops_context(ctx, start_ts, result)
                print(json.dumps(result, indent=2))
                sys.exit(0 if gate_result.get("passed") else 1)
            finally:
                _finalize_ops_context(
                    ctx, start_ts, {"status": "completed"}, finalize_only=True
                )
        elif args.command == "ops-regression-gate":
            from quantum_edge_core.supervisor.supervisor.policy_store import load_active_policy
            from quantum_edge_core.supervisor.supervisor.ops.regression_gates import run_regression_gates

            runtime_dir = app.paths.runtime_dir
            candidate_path = Path(args.policy_path) if args.policy_path else None
            if candidate_path is None and args.policy_version:
                candidate_path = (
                    runtime_dir
                    / "policy_versions"
                    / f"policy_{args.policy_version}.yaml"
                )
            if candidate_path is None:
                print(
                    "Missing --policy-version or --policy-path for ops-regression-gate.",
                    file=sys.stderr,
                )
                sys.exit(1)
            _, _, active_path = load_active_policy(
                runtime_dir, supervisor_config_dir / "policy.yaml"
            )
            result = run_regression_gates(
                episode_set=args.episode_set or "tick_scenarios_v1",
                runtime_dir=runtime_dir,
                candidate_policy_path=candidate_path,
                baseline_policy_path=active_path,
                gate_suite=args.gate_suite,
            )
            if args.policy_version:
                out_dir = runtime_dir / "regression" / args.policy_version
                out_dir.mkdir(parents=True, exist_ok=True)
                (out_dir / "gate_report.json").write_text(
                    json.dumps(result, indent=2), encoding="utf-8"
                )
                (out_dir / "gate_report.md").write_text(
                    _render_gate_report_md(result), encoding="utf-8"
                )
            print(json.dumps(result, indent=2))
            sys.exit(0 if result.get("passed") else 1)
        elif args.command == "ops-daily-report":
            from quantum_edge_core.supervisor.supervisor.ops.daily_report import generate_daily_report

            target_date = date.today()
            if args.date:
                try:
                    target_date = date.fromisoformat(args.date)
                except ValueError:
                    print("Invalid date format. Use YYYY-MM-DD.", file=sys.stderr)
                    sys.exit(1)
            runtime_dir = app.paths.runtime_dir
            telemetry_path = None
            if app.config.telemetry_persist_path:
                telemetry_path = Path(app.config.telemetry_persist_path)
                if not telemetry_path.is_absolute():
                    telemetry_path = (app.paths.qe_root / telemetry_path).resolve()
            report_dir = runtime_dir / "reports" / "daily"
            report_path = generate_daily_report(
                target_date, runtime_dir, report_dir, telemetry_path=telemetry_path
            )
            print(f"Daily report written to: {report_path}")
        elif args.command == "ops-rollback":
            from quantum_edge_core.supervisor.supervisor.policy_store import rollback_to

            if not args.policy_version:
                print("Missing --policy-version for ops-rollback.", file=sys.stderr)
                sys.exit(1)
            runtime_dir = app.paths.runtime_dir
            path = rollback_to(runtime_dir, args.policy_version)
            print(f"Active policy set to {path}")
        elif args.command == "autopilot-status":
            print(json.dumps(autopilot_status(app.autopilot), indent=2))
        elif args.command == "autopilot-enable":
            result = app.autopilot_set_enabled(True)
            print(json.dumps(result, indent=2))
        elif args.command == "autopilot-disable":
            result = app.autopilot_set_enabled(False)
            print(json.dumps(result, indent=2))
        elif args.command == "policy-list":
            manager = app.policy_manager_for(args.symbol)
            print(json.dumps(policy_list(manager), indent=2))
        elif args.command == "policy-rollout":
            manager = app.policy_manager_for(args.symbol)
            if not args.path:
                print("Missing --path for policy-rollout.", file=sys.stderr)
                sys.exit(1)
            print(
                json.dumps(
                    policy_rollout(
                        manager,
                        Path(args.path),
                        reason="manual_rollout",
                        audit=app.autopilot.audit,
                    ),
                    indent=2,
                )
            )
        elif args.command == "policy-rollback":
            manager = app.policy_manager_for(args.symbol)
            print(
                json.dumps(
                    policy_rollback(
                        manager, reason="manual_rollback", audit=app.autopilot.audit
                    ),
                    indent=2,
                )
            )
        elif args.command == "lockbot-policy-status":
            print(json.dumps(app.lockbot_policy_status(), indent=2))
        elif args.command == "lockbot-policy-enable":
            print(json.dumps(app.lockbot_policy_set_enabled(True), indent=2))
        elif args.command == "lockbot-policy-disable":
            print(json.dumps(app.lockbot_policy_set_enabled(False), indent=2))
        elif args.command == "lockbot-policy-decisions":
            print(json.dumps(app.lockbot_policy_decisions(args.limit), indent=2))
        elif args.command == "lockbot-exec-arm":
            print(
                json.dumps(
                    app.lockbot_execution_arm(
                        args.exec_mode, args.exec_ttl_s, args.exec_reason
                    ),
                    indent=2,
                )
            )
        elif args.command == "lockbot-exec-disarm":
            print(json.dumps(app.lockbot_execution_disarm(args.exec_reason), indent=2))
        elif args.command == "lockbot-exec-cancel-all":
            print(
                json.dumps(
                    app.lockbot_execution_cancel_all(args.exec_scope, args.exec_reason),
                    indent=2,
                )
            )
        elif args.command == "lockbot-exec-status":
            print(json.dumps(app.lockbot_execution_status(args.limit), indent=2))
    except Exception as exc:
        logging.getLogger(__name__).exception(
            "Command '%s' failed: %s", args.command, exc
        )
        sys.exit(1)


def _coerce_float(value: object) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _init_ops_context(
    project_root: Path,
    policy_version: str,
    note: str,
    config_snapshot: Dict[str, Any],
) -> tuple[RunContext, ActionLedger, float]:
    ctx = RunContext.create(
        project_root=project_root,
        policy_version=policy_version,
        model_version="none",
        note=note,
    )
    ctx.write_config_snapshot({"ops_command": note, "payload": config_snapshot})
    ctx.log_event("RUN_START", {"command": note})
    ledger = ActionLedger(ctx.run_dir / "action_ledger.jsonl", ctx)
    return ctx, ledger, time.time()


def _finalize_ops_context(
    ctx: RunContext,
    start_ts: float,
    result: Dict[str, Any],
    finalize_only: bool = False,
) -> None:
    if getattr(ctx, "_ops_finalized", False):
        return
    if finalize_only and (ctx.run_dir / "summary.json").exists():
        return
    duration = int(time.time() - start_ts)
    ctx.log_event("RUN_END", {"duration_s": duration, "result": result})
    summary = {
        "start_ts_utc": datetime.fromtimestamp(start_ts, tz=timezone.utc).isoformat(),
        "end_ts_utc": datetime.now(timezone.utc).isoformat(),
        "duration_s": duration,
        "errors_count": ctx.errors_count,
        "result": result,
    }
    ctx.write_summary(summary)
    ctx.write_artifacts_manifest()
    ctx._ops_finalized = True


def _last_run_has_critical_events(runs_dir: Path) -> bool:
    critical_types = {"ERROR", "KILL_SWITCH", "RISK_LIMIT_BREACH", "HALT"}
    if not runs_dir.exists():
        return False
    run_dirs = sorted(
        [p for p in runs_dir.iterdir() if p.is_dir()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for run_dir in run_dirs:
        summary_path = run_dir / "summary.json"
        if summary_path.exists():
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                summary = {}
            if int(summary.get("errors_count") or 0) > 0:
                return True
            events_path = run_dir / "events.jsonl"
            if events_path.exists():
                try:
                    for line in events_path.read_text(encoding="utf-8").splitlines():
                        if not line.strip():
                            continue
                        payload = json.loads(line)
                        if str(payload.get("type")) in critical_types:
                            return True
                except json.JSONDecodeError:
                    return True
            break
    return False


def _render_gate_report_md(result: Dict[str, Any]) -> str:
    lines = [
        "# Regression Gate Report",
        "",
        f"Passed: {bool(result.get('passed'))}",
        f"Gate suite: {result.get('gate_suite')}",
        "",
        "## Checks",
    ]
    for check in result.get("checks", []):
        status = "PASS" if check.get("passed") else "FAIL"
        lines.append(
            f"- [{status}] {check.get('name')}: actual={check.get('actual')} limit={check.get('limit')}"
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
