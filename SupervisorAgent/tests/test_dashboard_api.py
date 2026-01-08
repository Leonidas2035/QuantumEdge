from dataclasses import replace
import importlib.util
from pathlib import Path

from supervisor.config import (
    load_autopilot_config,
    load_dashboard_config,
    load_llm_supervisor_config,
    load_market_risk_config,
    load_meta_supervisor_config,
    load_paths_config,
    load_risk_config,
    load_snapshot_scheduler_config,
    load_supervisor_config,
    load_trading_behavior_config,
    load_trend_evaluator_config,
    load_tsdb_config,
    load_tsdb_retention_config,
)
from supervisor.config_loader import load_processes_spec
from supervisor.guards import load_guard_config
from supervisor.policy_store import resolve_active_policy_path
from supervisor.regime_sm import load_directives_config, load_regime_config


def _load_supervisor_app_class():
    module_path = Path(__file__).resolve().parents[2] / "SupervisorAgent" / "supervisor.py"
    spec = importlib.util.spec_from_file_location("supervisor_app_module", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load SupervisorApp module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.SupervisorApp


def _build_test_app(tmp_path: Path):
    SupervisorApp = _load_supervisor_app_class()
    project_root = Path(__file__).resolve().parents[2]
    supervisor_dir = project_root / "SupervisorAgent"
    config_dir = supervisor_dir / "config"
    paths_config_path = project_root / "config" / "paths.yaml"
    paths = load_paths_config(paths_config_path)

    runtime_dir = tmp_path / "runtime"
    logs_dir = tmp_path / "logs"
    events_dir = logs_dir / "events"
    reports_dir = tmp_path / "reports"
    for path in (runtime_dir, logs_dir, events_dir, reports_dir):
        path.mkdir(parents=True, exist_ok=True)
    paths = replace(
        paths,
        runtime_dir=runtime_dir,
        logs_dir=logs_dir,
        events_dir=events_dir,
        reports_dir=reports_dir,
    )

    supervisor_config = load_supervisor_config(config_dir / "supervisor.yaml")
    supervisor_config = replace(
        supervisor_config,
        policy_file_path=str(runtime_dir / "policy.json"),
        telemetry_persist_path=str(runtime_dir / "telemetry_store.jsonl"),
        api_enabled=False,
    )
    processes_path = Path(supervisor_config.processes_file)
    if not processes_path.is_absolute():
        processes_path = (project_root / processes_path).resolve()
    if not processes_path.exists():
        fallback = config_dir / processes_path.name
        if fallback.exists():
            processes_path = fallback
    try:
        process_specs = load_processes_spec(processes_path, paths.qe_root)
    except Exception:
        process_specs = {}
    risk_config = load_risk_config(config_dir / "risk.yaml")
    llm_config = load_llm_supervisor_config(config_dir / "llm_supervisor.yaml")
    trend_config = load_trend_evaluator_config(config_dir / "llm_trend_evaluator.yaml")
    market_risk_config = load_market_risk_config(config_dir / "llm_market_risk.yaml")
    behavior_config = load_trading_behavior_config(config_dir / "llm_trading_behavior.yaml")
    snapshot_config = load_snapshot_scheduler_config(config_dir / "supervisor.yaml")
    meta_config = load_meta_supervisor_config(config_dir / "meta_supervisor.yaml", paths)
    dashboard_config = load_dashboard_config(config_dir / "dashboard.yaml")
    tsdb_config = load_tsdb_config(config_dir / "tsdb.yaml")
    tsdb_retention = load_tsdb_retention_config(config_dir / "tsdb_retention.yaml")
    autopilot_cfg = load_autopilot_config(config_dir / "autopilot.yaml")
    control_policy_path = resolve_active_policy_path(paths.runtime_dir, config_dir / "policy.yaml")
    regime_cfg = load_regime_config(control_policy_path)
    guard_cfg = load_guard_config(control_policy_path)
    directives_cfg = load_directives_config(control_policy_path)

    return SupervisorApp(
        paths,
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
        regime_cfg,
        guard_cfg,
        directives_cfg,
        autopilot_cfg,
        process_specs,
        project_root,
    )


def test_dashboard_api_shapes(tmp_path: Path) -> None:
    app = _build_test_app(tmp_path)
    app.dashboard_store.ingest_event(
        {
            "type": "strategy_telemetry.v1",
            "data": {"strategy_id": "SCALP", "symbol": "BTCUSDT", "position_notional": 10},
        }
    )
    app.dashboard_store.ingest_event(
        {
            "type": "strategy_limits.v1",
            "data": {"strategy_id": "SCALP", "symbol": "BTCUSDT", "max_position_notional": 100},
        }
    )
    app.dashboard_store.ingest_event(
        {
            "type": "scalp_deal_closed.v1",
            "data": {"strategy_id": "SCALP", "symbol": "BTCUSDT", "deal_id": "s1", "net_pnl": 1, "fees": 0.1},
        }
    )

    overview = app.dashboard_overview()
    assert "strategies_total" in overview
    assert "alerts_active" in overview
    strategies = app.dashboard_strategies()
    assert isinstance(strategies.get("strategies"), list)
    performance = app.dashboard_performance()
    assert "session" in performance
    alerts = app.dashboard_alerts()
    assert "active" in alerts and "recent" in alerts
    reset = app.dashboard_reset_counters()
    assert reset.get("status") == "ok"
    audit = app.dashboard_audit(limit=50)
    assert isinstance(audit.get("items"), list)
