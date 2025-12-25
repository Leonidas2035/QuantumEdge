"""CLI entrypoint for SupervisorAgent."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime, date, timezone
from pathlib import Path
from typing import Optional, Mapping, Any, Dict, List
import threading

from supervisor.config import (
    load_paths_config,
    load_supervisor_config,
    load_risk_config,
    load_llm_supervisor_config,
    load_meta_supervisor_config,
    load_trend_evaluator_config,
    load_market_risk_config,
    load_trading_behavior_config,
    load_snapshot_scheduler_config,
    load_dashboard_config,
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
    TsdbConfig,
    TsdbRetentionConfig,
)
from supervisor.heartbeat import HeartbeatServer, HeartbeatPayload
from supervisor.logging_setup import setup_logging
from supervisor.process_manager import ProcessManager, ProcessInfo
from supervisor.risk_engine import RiskEngine, RiskDecision, OrderRequest, OrderSide, OrderType
from supervisor import state as state_utils
from supervisor.events import EventLogger
from supervisor.audit_report import load_events_for_date, compute_stats, render_markdown_report
from supervisor.llm_supervisor import LlmSupervisor, LlmSupervisorAdvice
from supervisor.llm.chat_client import ChatCompletionsClient
from supervisor.llm.trend_evaluator import TrendEvaluator
from supervisor.llm.market_risk_monitor import MarketRiskMonitor
from supervisor.llm.trading_behavior_analyzer import TradingBehaviorAnalyzer
from supervisor.meta_supervisor import MetaSupervisorRunner, MetaSupervisorContext
from supervisor.api_server import ApiServer, ApiServerConfig
from supervisor.snapshot_models import SnapshotReport
from supervisor.tasks.snapshot_scheduler import SnapshotScheduler
from supervisor.dashboard.service import DashboardService
from supervisor.tsdb import NoopTimeseriesStore, ClickHouseTimeseriesStore, QuestDbTimeseriesStore, TsdbWriter
from supervisor.tsdb.maintenance import apply_retention_and_rollups
from policy.policy_contract import policy_fingerprint
from policy.policy_publisher import PolicyPublisher
from policy.policy_engine import PolicyEngine, PolicyEngineConfig, HysteresisConfig
from policy.heuristics import HeuristicThresholds
from monitoring.api import TelemetryManager, TelemetryConfig

try:
    from tools.qe_config import get_qe_paths
except Exception:  # pragma: no cover - fallback for legacy runs
    get_qe_paths = None


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
        tsdb_config: TsdbConfig,
        tsdb_retention: TsdbRetentionConfig,
        project_root: Path,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.paths = paths
        self.config = config
        self.risk_config = risk_config
        self.llm_config = llm_config
        self.trend_config = trend_config
        self.market_risk_config = market_risk_config
        self.behavior_config = behavior_config
        self.snapshot_config = snapshot_config
        self.meta_config = meta_config
        self.project_root = project_root
        self.tsdb_config = tsdb_config
        self.logger = logger or logging.getLogger(__name__)
        self.state_dir = paths.runtime_dir / "supervisor"
        self.state_dir.mkdir(parents=True, exist_ok=True)

        events_path = paths.events_dir / f"events_{date.today().isoformat()}.jsonl"
        self.snapshots_dir = paths.logs_dir / "snapshots"
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.event_logger = EventLogger(events_path, self.logger, snapshots_dir=self.snapshots_dir)
        # TSDB wiring
        self.tsdb_backend = "none"
        self.tsdb_writer = self._build_tsdb_writer(tsdb_config)
        if self.tsdb_writer:
            self.tsdb_writer.start()
            self.event_logger.tsdb_writer = self.tsdb_writer
        self.heartbeat_server = HeartbeatServer(config.heartbeat_timeout_s)
        risk_state = state_utils.load_risk_state(self.state_dir, today=date.today())
        self.risk_engine = RiskEngine(risk_config, risk_state, self.logger, self.event_logger, llm_config.trust_policy)
        self.process_manager = ProcessManager(paths, config, self.state_dir, self.event_logger, self.logger)
        self.llm_client = ChatCompletionsClient(llm_config.api_url, llm_config.api_key_env, self.logger)
        self.llm_supervisor = LlmSupervisor(llm_config, risk_config, paths.events_dir, self.logger, self.event_logger, chat_client=self.llm_client)
        self.trend_evaluator = TrendEvaluator(trend_config, self.llm_client, self.logger)
        self.market_risk_monitor = MarketRiskMonitor(market_risk_config, self.llm_client, self.logger)
        self.behavior_analyzer = TradingBehaviorAnalyzer(behavior_config, self.llm_client, self.logger)
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
        # Dashboard service
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
        self.api_server = ApiServer(api_config, self, self.logger) if config.api_enabled else None
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
        telemetry_persist = Path(config.telemetry_persist_path) if config.telemetry_persist_path else None
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
        self.policy_engine = PolicyEngine(engine_cfg, self.paths, self.process_manager, self.risk_engine, self.logger, telemetry_manager=self.telemetry)
        self.policy_publisher = PolicyPublisher(policy_file, self.logger)
        self.policy_publish_interval_s = float(config.policy_publish_interval_s)
        self._last_policy_fingerprint: Optional[str] = None
        self._current_policy = None

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
            self.logger.warning("TSDB backend init failed; continuing without TSDB: %s", exc)
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
        info = self.process_manager.start(self.config.mode)
        self.logger.info("Bot started with PID %s", info.pid)

    def stop(self) -> None:
        self.process_manager.stop()
        self.logger.info("Bot stopped.")

    def restart(self) -> None:
        info = self.process_manager.restart(self.config.mode)
        self.logger.info("Bot restarted with PID %s", info.pid)

    def get_bot_status(self) -> Dict[str, Any]:
        return self.process_manager.get_status_payload()

    def get_policy_payload(self) -> Dict[str, Any]:
        policy = self._current_policy or self.policy_engine.current_policy()
        return policy.to_dict()

    def get_policy_debug(self) -> Dict[str, Any]:
        return self.policy_engine.debug_payload()

    def ingest_telemetry_event(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.telemetry.ingest(payload)

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
        except Exception:  # noqa: BLE001
            self.logger.exception("Policy publish failed")

    def start_bot(self) -> Dict[str, Any]:
        try:
            self.process_manager.start(self.config.mode)
        except Exception as exc:
            self.logger.error("Bot start failed: %s", exc)
        return self.get_bot_status()

    def stop_bot(self) -> Dict[str, Any]:
        self.process_manager.stop()
        return self.get_bot_status()

    def restart_bot(self) -> Dict[str, Any]:
        try:
            self.process_manager.restart(self.config.mode)
        except Exception as exc:
            self.logger.error("Bot restart failed: %s", exc)
        return self.get_bot_status()

    def status(self) -> None:
        running = self.process_manager.is_running()
        info = self.process_manager.get_info()
        self._print_status(running, info)

    def run_foreground(self) -> None:
        """Run supervisor loop, restarting the child if it dies."""

        next_llm_check_at = time.time() + (self.llm_config.check_interval_minutes * 60 if self.llm_config.enabled else 0)
        snapshot_interval = self.snapshot_config.interval_minutes * 60 if self.snapshot_config.enabled else None
        next_snapshot_at = time.time() + snapshot_interval if snapshot_interval else float("inf")
        next_policy_publish_at = 0.0
        if self.api_server:
            self.api_server.start()
        try:
            while True:
                self.process_manager.tick(self.config.mode)
                self.telemetry.update_process_state(self.process_manager.get_status_payload())
                if time.time() >= next_policy_publish_at:
                    self._publish_policy()
                    next_policy_publish_at = time.time() + self.policy_publish_interval_s
                if (
                    self.llm_config.enabled
                    and time.time() >= next_llm_check_at
                    and not self.risk_engine.state.halted
                ):
                    try:
                        self.run_llm_check_once()
                    except Exception as exc:
                        self.logger.error("LLM check failed: %s", exc)
                    next_llm_check_at = time.time() + self.llm_config.check_interval_minutes * 60
                if snapshot_interval and time.time() >= next_snapshot_at:
                    try:
                        self.run_snapshot_once(verbose=False)
                    except Exception as exc:
                        self.logger.error("Snapshot generation failed: %s", exc)
                    next_snapshot_at = time.time() + snapshot_interval
                time.sleep(2.0)
        except KeyboardInterrupt:
            self.logger.info("Received interrupt; stopping.")
        except Exception:  # noqa: BLE001
            self.logger.exception("Supervisor loop crashed")
            raise
        finally:
            if self.api_server:
                self.api_server.stop()
            self.process_manager.stop()

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
        print(f"Limits: daily_loss_abs={self.risk_config.max_daily_loss_abs}, "
              f"daily_loss_pct={self.risk_config.max_daily_loss_pct}, "
              f"drawdown_abs={self.risk_config.max_drawdown_abs}, "
              f"drawdown_pct={self.risk_config.max_drawdown_pct}, "
              f"max_notional_per_symbol={self.risk_config.max_notional_per_symbol}, "
              f"max_leverage={self.risk_config.max_leverage}")

    def _print_status(self, running: bool, info: Optional[ProcessInfo]) -> None:
        heartbeat_state = self.heartbeat_server.get_state()
        heartbeat_status = heartbeat_state.status
        risk_state = self.risk_engine.get_state()
        state = self.process_manager.get_state()

        print("Supervisor status")
        print("=================")
        if running and info:
            uptime = (datetime.now(info.start_time.tzinfo) - info.start_time).total_seconds() if info.start_time else None
            uptime_str = f"{uptime:.0f}s" if uptime is not None else "unknown"
            print(f"Bot: {state} (pid={info.pid}, uptime={uptime_str})")
        elif info:
            exit_code = info.last_exit_code if info.last_exit_code is not None else "unknown"
            exit_time = info.last_exit_time.isoformat() if info.last_exit_time else "unknown time"
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
            print(f"  realized_pnl_today: {risk_state.realized_pnl_today:.2f} {self.risk_config.currency}")

        print("  limits:")
        print(f"    max_daily_loss_abs: {self.risk_config.max_daily_loss_abs} {self.risk_config.currency}")
        if self.risk_config.max_daily_loss_pct is not None:
            print(f"    max_daily_loss_pct: {self.risk_config.max_daily_loss_pct:.2%}")
        if self.risk_config.max_drawdown_abs is not None:
            print(f"    max_drawdown_abs: {self.risk_config.max_drawdown_abs} {self.risk_config.currency}")
        if self.risk_config.max_drawdown_pct is not None:
            print(f"    max_drawdown_pct: {self.risk_config.max_drawdown_pct:.2%}")
        print(f"    max_notional_per_symbol: {self.risk_config.max_notional_per_symbol}")
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
            self.logger.warning("Order blocked: %s - %s", decision.code, decision.reason)
        return decision

    def audit(self, target_date: date) -> None:
        """Generate audit report for a given date."""

        events = load_events_for_date(self.paths.events_dir, target_date)
        if not events:
            print(f"No events found for {target_date.isoformat()} in {self.paths.events_dir}")
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

        if not self.llm_config.enabled:
            print("LLM supervisor is disabled.")
            return

        snapshot = state_utils.load_risk_state(self.state_dir, today=date.today())
        self.risk_engine.state = snapshot
        advice = self.llm_supervisor.run_check(date.today(), snapshot, mode=self.config.mode)
        if advice is None:
            print("LLM check produced no advice (disabled, insufficient data, or error).")
            return

        print(f"LLM Advice: action={advice.action.value}, risk_multiplier={advice.risk_multiplier}, comment={advice.comment}")

        if self.llm_config.dry_run:
            self.logger.info("LLM advice received (dry-run): %s", advice)
            return

        self.risk_engine.apply_llm_advice(advice)
        self.risk_engine.persist(self.state_dir)
        print("Advice applied to risk state.")

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
            "last_heartbeat_time": state.last_heartbeat_time.isoformat() if state.last_heartbeat_time else None,
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
                price=float(payload["price"]) if payload.get("price") is not None else None,
                notional=float(payload["notional"]) if payload.get("notional") is not None else None,
                leverage=float(payload["leverage"]) if payload.get("leverage") is not None else None,
                is_reduce_only=bool(payload.get("is_reduce_only", False)),
            )
        except ValueError as exc:
            raise ValueError(f"Invalid enum value: {exc}") from exc

        with self._lock:
            decision = self.risk_engine.evaluate_order(order_request)
            snapshot = self.risk_engine.get_state()
            self.risk_engine.persist(self.state_dir)

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
            uptime = (datetime.now(info.start_time.tzinfo) - info.start_time).total_seconds() if info.start_time else None
            bot_data.update({"pid": info.pid, "uptime_seconds": uptime})
        elif info:
            bot_data.update({"last_exit_code": info.last_exit_code, "last_exit_time": info.last_exit_time.isoformat() if info.last_exit_time else None})

        return {
            "bot": bot_data,
            "heartbeat": {
                "status": heartbeat_state.status,
                "last_heartbeat_time": heartbeat_state.last_heartbeat_time.isoformat() if heartbeat_state.last_heartbeat_time else None,
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
        if not self.dashboard_service or not self.dashboard_service.enabled:
            return {"status": "disabled"}
        overview = self.dashboard_service.get_overview()
        return {
            "timestamp": overview.timestamp.isoformat(),
            "total_pnl": overview.total_pnl,
            "pnl_1h": overview.pnl_1h,
            "open_positions": overview.open_positions,
            "open_orders": overview.open_orders,
            "strategy_mode": overview.strategy_mode,
            "market_trend": overview.market_trend,
            "market_risk_level": overview.market_risk_level,
        }

    def dashboard_health(self) -> Dict[str, Any]:
        if not self.dashboard_service or not self.dashboard_service.enabled:
            return {"status": "disabled"}
        health = self.dashboard_service.get_health()
        return {
            "status": health.status,
            "issues": health.issues,
            "last_heartbeat_at": health.last_heartbeat_at.isoformat() if health.last_heartbeat_at else None,
            "last_snapshot_at": health.last_snapshot_at.isoformat() if health.last_snapshot_at else None,
        }

    def dashboard_events(self, limit: Optional[int] = None, types: Optional[List[str]] = None) -> List[Dict[str, Any]]:
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

    def get_tsdb_status(self) -> Dict[str, Any]:
        enabled = bool(self.tsdb_writer)
        reachable = None
        if enabled and self.tsdb_backend == "clickhouse":
            import urllib.request
            try:
                with urllib.request.urlopen(f"{self.tsdb_config.clickhouse_url}/ping", timeout=3) as resp:  # noqa: S310
                    reachable = resp.status == 200 and resp.read() in (b"Ok.", b"Ok.\n")
            except Exception:
                reachable = False
        elif enabled and self.tsdb_backend == "questdb":
            # QuestDB ILP has no ping; assume reachable if writer exists
            reachable = True
        return {
            "enabled": enabled,
            "backend": self.tsdb_backend,
            "reachable": reachable,
            "last_write_at": self.tsdb_writer.last_write_at.isoformat() if enabled and self.tsdb_writer.last_write_at else None,
            "queue_depth": self.tsdb_writer.queue_depth if enabled else 0,
        }

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
            add("OK", f"TrendEvaluator config loaded (window={self.trend_config.history_window_minutes}m)")
        else:
            add("WARN", "TrendEvaluator disabled")

        if self.market_risk_config.enabled:
            add("OK", f"MarketRiskMonitor config loaded (history={self.market_risk_config.history_window_minutes}m)")
        else:
            add("WARN", "MarketRiskMonitor disabled")

        if self.behavior_config.enabled:
            add("OK", f"TradingBehaviorAnalyzer history trades={self.behavior_config.history_trades}")
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

        if self.tsdb_writer:
            add("OK", f"TSDB enabled (backend={self.tsdb_backend})")
            status = self.get_tsdb_status()
            if status.get("reachable") is False:
                add("WARN", "TSDB backend unreachable (see /api/v1/tsdb/status)")
        else:
            add("WARN", "TSDB disabled or using noop backend")
        if self.tsdb_retention.enabled:
            add("OK", f"TSDB retention config loaded (raw_days={self.tsdb_retention.raw_days})")
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
            "tsdb-backfill",
            "tsdb-migrate",
            "tsdb-maintain",
            "ml",
            "telemetry",
            "research",
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
        "--config",
        dest="config_path",
        help="Path to supervisor config YAML (defaults to QE_ROOT/config/supervisor.yaml).",
    )
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
    setup_logging(paths_config.logs_dir)
    supervisor_config = load_supervisor_config(supervisor_config_path)
    risk_config = load_risk_config(supervisor_config_dir / "risk.yaml")
    llm_config = load_llm_supervisor_config(supervisor_config_dir / "llm_supervisor.yaml")
    trend_config = load_trend_evaluator_config(supervisor_config_dir / "llm_trend_evaluator.yaml")
    market_risk_config = load_market_risk_config(supervisor_config_dir / "llm_market_risk.yaml")
    behavior_config = load_trading_behavior_config(supervisor_config_dir / "llm_trading_behavior.yaml")
    snapshot_config = load_snapshot_scheduler_config(supervisor_config_path)
    meta_config = load_meta_supervisor_config(supervisor_config_dir / "meta_supervisor.yaml", paths_config)
    dashboard_config = load_dashboard_config(supervisor_config_dir / "dashboard.yaml")
    tsdb_config = load_tsdb_config(supervisor_config_dir / "tsdb.yaml")
    tsdb_retention = load_tsdb_retention_config(supervisor_config_dir / "tsdb_retention.yaml")
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
        tsdb_config,
        tsdb_retention,
        project_root,
        logging.getLogger(__name__),
    )


def main(argv: Optional[list[str]] = None) -> None:
    args = parse_args(argv)
    project_root = Path(__file__).resolve().parent

    qe_paths = None
    if get_qe_paths:
        try:
            qe_paths = get_qe_paths()
        except Exception:
            qe_paths = None

    qe_root = Path(os.getenv("QE_ROOT") or (qe_paths["qe_root"] if qe_paths else project_root.parent))
    os.environ.setdefault("QE_ROOT", str(qe_root))

    config_dir = Path(os.getenv("QE_CONFIG_DIR") or (qe_paths["config_dir"] if qe_paths else qe_root / "config"))
    supervisor_config_dir = Path(qe_paths["supervisor_config_dir"] if qe_paths else project_root / "config")
    project_root = Path(qe_paths["supervisor_dir"] if qe_paths else project_root)

    if not supervisor_config_dir.exists():
        supervisor_config_dir = project_root / "config"

    paths_config_path = config_dir / "paths.yaml"
    if not paths_config_path.exists():
        paths_config_path = project_root / "config" / "paths.yaml"

    supervisor_config_path = Path(args.config_path) if args.config_path else Path(os.getenv("SUPERVISOR_CONFIG") or config_dir / "supervisor.yaml")
    if not supervisor_config_path.exists():
        supervisor_config_path = supervisor_config_dir / "supervisor.yaml"

    try:
        app = build_app(project_root, paths_config_path, supervisor_config_path, supervisor_config_dir)
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
        elif args.command == "tsdb-migrate":
            from supervisor.tsdb.migrations import run_tsdb_migrations

            ok = run_tsdb_migrations(project_root, app.tsdb_config, logging.getLogger(__name__), retention=app.tsdb_retention)
            sys.exit(0 if ok else 1)
        elif args.command == "tsdb-backfill":
            if not app.tsdb_config.enabled or app.tsdb_config.backend == "none":
                print("TSDB is disabled; backfill skipped.")
                sys.exit(0)
            days = args.days or app.tsdb_config.backfill_from_days
            from supervisor.tsdb.backfill import run_backfill

            store = app.tsdb_writer.store if app.tsdb_writer else None
            if store is None:
                print("TSDB writer not initialized; cannot backfill.")
                sys.exit(1)
            checkpoint = app.state_dir / "tsdb_backfill_state.json"
            run_backfill(app.paths.events_dir, store, days, checkpoint, logging.getLogger(__name__))
            print(f"Backfill completed for last {days} day(s).")
        elif args.command == "tsdb-maintain":
            ok = apply_retention_and_rollups(project_root, app.tsdb_config, app.tsdb_retention, logging.getLogger(__name__))
            sys.exit(0 if ok else 1)
        elif args.command == "ml":
            from SupervisorAgent.mlops.cli import parse_ml_args, run_ml_command

            ml_args = parse_ml_args(args.ml_args)
            code = run_ml_command(ml_args)
            sys.exit(code)
        elif args.command == "telemetry":
            from SupervisorAgent.monitoring.cli import parse_telemetry_args, run_telemetry_command

            telemetry_args = parse_telemetry_args(args.ml_args)
            code = run_telemetry_command(app, telemetry_args)
            sys.exit(code)
        elif args.command == "research":
            try:
                from SupervisorAgent.research.cli import parse_research_args, run_research_command
            except ModuleNotFoundError:
                from research.cli import parse_research_args, run_research_command

            research_args = parse_research_args(args.ml_args)
            code = run_research_command(research_args)
            sys.exit(code)
    except Exception as exc:
        logging.getLogger(__name__).exception("Command '%s' failed: %s", args.command, exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
