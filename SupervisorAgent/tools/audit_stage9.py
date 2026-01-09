"""Stage 9.1-9.4 audit harness for SupervisorAgent."""

from __future__ import annotations

import importlib.util
import sys
import subprocess
import tempfile
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple
ROOT = Path(__file__).resolve().parents[2]
SUPERVISOR_DIR = ROOT / "SupervisorAgent"
CONFIG_DIR = SUPERVISOR_DIR / "config"

if str(SUPERVISOR_DIR) not in sys.path:
    sys.path.insert(0, str(SUPERVISOR_DIR))


def _load_supervisor_app_class():
    module_path = SUPERVISOR_DIR / "supervisor.py"
    spec = importlib.util.spec_from_file_location("supervisor_app_module", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load SupervisorApp module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.SupervisorApp


def _git_info() -> Tuple[str, str]:
    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip()
    except Exception:
        sha = "unknown"
    try:
        branch = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=ROOT).decode().strip()
    except Exception:
        branch = "unknown"
    return sha, branch


def _build_test_app(tmp_root: Path):
    SupervisorApp = _load_supervisor_app_class()
    from supervisor.config import (
        load_autopilot_config,
        load_dashboard_config,
        load_llm_supervisor_config,
        load_market_risk_config,
        load_meta_supervisor_config,
        load_lockbot_config,
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
    paths = load_paths_config(ROOT / "config" / "paths.yaml")
    runtime_dir = tmp_root / "runtime"
    logs_dir = tmp_root / "logs"
    events_dir = logs_dir / "events"
    reports_dir = tmp_root / "reports"
    for path in (runtime_dir, logs_dir, events_dir, reports_dir):
        path.mkdir(parents=True, exist_ok=True)
    paths = replace(
        paths,
        runtime_dir=runtime_dir,
        logs_dir=logs_dir,
        events_dir=events_dir,
        reports_dir=reports_dir,
    )
    supervisor_config = load_supervisor_config(CONFIG_DIR / "supervisor.yaml")
    supervisor_config = replace(
        supervisor_config,
        policy_file_path=str(runtime_dir / "policy.json"),
        telemetry_persist_path=str(runtime_dir / "telemetry_store.jsonl"),
        api_enabled=False,
    )
    processes_path = Path(supervisor_config.processes_file)
    if not processes_path.is_absolute():
        processes_path = (ROOT / processes_path).resolve()
    if not processes_path.exists():
        fallback = CONFIG_DIR / processes_path.name
        if fallback.exists():
            processes_path = fallback
    try:
        process_specs = load_processes_spec(processes_path, paths.qe_root)
    except Exception:
        process_specs = {}
    risk_config = load_risk_config(CONFIG_DIR / "risk.yaml")
    llm_config = load_llm_supervisor_config(CONFIG_DIR / "llm_supervisor.yaml")
    trend_config = load_trend_evaluator_config(CONFIG_DIR / "llm_trend_evaluator.yaml")
    market_risk_config = load_market_risk_config(CONFIG_DIR / "llm_market_risk.yaml")
    behavior_config = load_trading_behavior_config(CONFIG_DIR / "llm_trading_behavior.yaml")
    snapshot_config = load_snapshot_scheduler_config(CONFIG_DIR / "supervisor.yaml")
    meta_config = load_meta_supervisor_config(CONFIG_DIR / "meta_supervisor.yaml", paths)
    dashboard_config = load_dashboard_config(CONFIG_DIR / "dashboard.yaml")
    lockbot_cfg = load_lockbot_config(CONFIG_DIR / "lockbot.yaml")
    lockbot_cfg = replace(lockbot_cfg, enabled=False)
    tsdb_config = load_tsdb_config(CONFIG_DIR / "tsdb.yaml")
    tsdb_retention = load_tsdb_retention_config(CONFIG_DIR / "tsdb_retention.yaml")
    autopilot_cfg = load_autopilot_config(CONFIG_DIR / "autopilot.yaml")
    control_policy_path = resolve_active_policy_path(paths.runtime_dir, CONFIG_DIR / "policy.yaml")
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
        lockbot_cfg,
        tsdb_config,
        tsdb_retention,
        regime_cfg,
        guard_cfg,
        directives_cfg,
        autopilot_cfg,
        process_specs,
        ROOT,
    )


def _check(condition: bool, label: str, details: str = "") -> Dict[str, Any]:
    return {"label": label, "ok": condition, "details": details}


def _static_checks() -> List[Dict[str, Any]]:
    checks = []
    required = [
        SUPERVISOR_DIR / "supervisor" / "dashboard" / "state_store.py",
        SUPERVISOR_DIR / "supervisor" / "dashboard" / "audit_log.py",
        SUPERVISOR_DIR / "static" / "index.html",
        SUPERVISOR_DIR / "static" / "app.js",
        SUPERVISOR_DIR / "config" / "alerts.yaml",
        ROOT / "ai_scalper_bot" / "bot" / "trading" / "deal_events.py",
    ]
    for path in required:
        checks.append(_check(path.exists(), f"exists:{path.relative_to(ROOT)}"))

    readme = SUPERVISOR_DIR / "README.md"
    if readme.exists():
        text = readme.read_text(encoding="utf-8")
        endpoints = [
            "/api/v1/dashboard/overview",
            "/api/v1/dashboard/strategies",
            "/api/v1/dashboard/performance",
            "/api/v1/dashboard/alerts",
            "/api/v1/dashboard/audit",
            "/api/v1/dashboard/reset-counters",
        ]
        missing = [ep for ep in endpoints if ep not in text]
        checks.append(_check(not missing, "docs:endpoints", f"missing={missing}" if missing else ""))
    else:
        checks.append(_check(False, "docs:readme_missing"))
    run_bot = ROOT / "ai_scalper_bot" / "bot" / "run_bot.py"
    scalp_emit = False
    if run_bot.exists():
        text = run_bot.read_text(encoding="utf-8")
        scalp_emit = "ScalpDealTracker" in text or "scalp_deal_closed.v1" in text
    deal_events = ROOT / "ai_scalper_bot" / "bot" / "trading" / "deal_events.py"
    if deal_events.exists():
        text = deal_events.read_text(encoding="utf-8")
        scalp_emit = scalp_emit or ("scalp_deal_closed.v1" in text)
        checks.append(_check("dca_deal_closed.v1" in text, "stage9.4:dca_deal_emit"))
    checks.append(_check(scalp_emit, "stage9.4:scalp_deal_emit"))
    return checks


def _api_checks(app: Any) -> List[Dict[str, Any]]:
    results = []
    overview = app.dashboard_overview()
    results.append(_check("strategies_total" in overview, "api:overview"))
    strategies = app.dashboard_strategies()
    results.append(_check(isinstance(strategies.get("strategies"), list), "api:strategies"))
    performance = app.dashboard_performance()
    results.append(_check("session" in performance, "api:performance"))
    alerts = app.dashboard_alerts()
    results.append(_check("active" in alerts and "recent" in alerts, "api:alerts"))
    audit = app.dashboard_audit(limit=10)
    results.append(_check(isinstance(audit.get("items"), list), "api:audit"))
    reset = app.dashboard_reset_counters()
    results.append(_check(reset.get("status") == "ok", "api:reset"))
    return results


def _alert_checks(app: Any) -> List[Dict[str, Any]]:
    results = []
    from supervisor.alerts.rules import load_alert_rules

    rules = load_alert_rules(CONFIG_DIR / "alerts.yaml")
    names = {rule.name for rule in rules}
    expected = {"telemetry_stale", "api_errors", "cancel_storm", "stuck_sells_dca", "limit_breaches"}
    results.append(_check(expected.issubset(names), "alerts:rules_present", f"missing={sorted(expected - names)}"))

    now_ms = int(time.time() * 1000)
    app.dashboard_store.ingest_event(
        {
            "type": "strategy_telemetry.v1",
            "data": {
                "strategy_id": "SCALP",
                "symbol": "BTCUSDT",
                "position_notional": 50,
                "api_errors_1m": 2,
                "cancel_count_1m": 30,
            },
            "ts_ms": now_ms - 6000,
        }
    )
    app.dashboard_store.ingest_event(
        {
            "type": "strategy_limits.v1",
            "data": {"strategy_id": "SCALP", "symbol": "BTCUSDT", "max_position_notional": 10},
            "ts_ms": now_ms,
        }
    )
    app.dashboard_store.ingest_event(
        {
            "type": "dca_lot_status.v1",
            "data": {"strategy_id": "DCA_ETH", "symbol": "ETHUSDT", "lot_id": "lot-1", "status": "SELL_PENDING"},
            "ts_ms": now_ms - 70000,
        }
    )
    start = time.time()
    app.dashboard_store.evaluate_alerts(now_ts=start)
    alert_result = app.dashboard_store.evaluate_alerts(now_ts=start + 25)
    active_rules = {item.get("rule") for item in alert_result.active if isinstance(item, dict)}
    results.append(_check("telemetry_stale" in active_rules, "alerts:stale_telemetry"))
    results.append(_check("api_errors" in active_rules, "alerts:api_errors"))
    results.append(_check("cancel_storm" in active_rules, "alerts:cancel_storm"))
    results.append(_check("stuck_sells_dca" in active_rules, "alerts:stuck_sells"))
    results.append(_check("limit_breaches" in active_rules, "alerts:limit_breaches"))

    # Resolve after clearing conditions
    clear_start = start + 70
    clear_ms = int(clear_start * 1000)
    app.dashboard_store.ingest_event(
        {
            "type": "strategy_telemetry.v1",
            "data": {"strategy_id": "SCALP", "symbol": "BTCUSDT", "position_notional": 5, "api_errors_1m": 0, "cancel_count_1m": 0},
            "ts_ms": clear_ms,
        }
    )
    app.dashboard_store.ingest_event(
        {
            "type": "dca_lot_status.v1",
            "data": {"strategy_id": "DCA_ETH", "symbol": "ETHUSDT", "lot_id": "lot-1", "status": "CLOSED"},
            "ts_ms": clear_ms,
        }
    )
    app.dashboard_store.evaluate_alerts(now_ts=clear_start)
    refresh_ms = int((clear_start + 35) * 1000)
    app.dashboard_store.ingest_event(
        {
            "type": "strategy_telemetry.v1",
            "data": {"strategy_id": "SCALP", "symbol": "BTCUSDT", "position_notional": 5, "api_errors_1m": 0, "cancel_count_1m": 0},
            "ts_ms": refresh_ms,
        }
    )
    app.dashboard_store.evaluate_alerts(now_ts=clear_start + 35)
    resolved_rules = {item.get("rule") for item in app.dashboard_store.alerts_snapshot().get("active", [])}
    results.append(_check("telemetry_stale" not in resolved_rules, "alerts:resolve"))

    audit_items = app.dashboard_audit(limit=200).get("items", [])
    has_active = any(item.get("event_type") == "alert_active" for item in audit_items)
    has_resolved = any(item.get("event_type") == "alert_resolved" for item in audit_items)
    results.append(_check(has_active and has_resolved, "alerts:audit_records"))
    return results


def _performance_checks(app: Any) -> List[Dict[str, Any]]:
    results = []
    app.dashboard_store.ingest_event(
        {
            "type": "dca_deal_closed.v1",
            "data": {"strategy_id": "DCA_ETH", "symbol": "ETHUSDT", "deal_id": "d1", "net_pnl": 5, "fees": 1, "volume_quote": 100},
        }
    )
    app.dashboard_store.ingest_event(
        {
            "type": "scalp_deal_closed.v1",
            "data": {"strategy_id": "SCALP", "symbol": "BTCUSDT", "deal_id": "s1", "net_pnl": -2, "fees": 0.5, "volume_quote": 50},
        }
    )
    perf = app.dashboard_performance()
    session = perf.get("session", {})
    results.append(_check(session.get("closed_deals") == 2, "perf:closed_deals"))
    results.append(_check(session.get("wins") == 1 and session.get("losses") == 1, "perf:wins_losses"))
    app.dashboard_reset_counters()
    perf_after = app.dashboard_performance()
    results.append(_check(perf_after.get("session", {}).get("closed_deals") == 0, "perf:reset"))

    audit_items = app.dashboard_audit(limit=200).get("items", [])
    results.append(_check(any(item.get("event_type") == "deal_closed" for item in audit_items), "perf:audit_deal_closed"))
    results.append(_check(any(item.get("event_type") == "counters_reset" for item in audit_items), "perf:audit_reset"))
    return results


def _ui_checks() -> List[Dict[str, Any]]:
    results = []
    html = (SUPERVISOR_DIR / "static" / "index.html").read_text(encoding="utf-8")
    js = (SUPERVISOR_DIR / "static" / "app.js").read_text(encoding="utf-8")
    for token in ("overview-strategies", "strategies-list", "performance-session", "audit-recent", "reset-counters"):
        results.append(_check(token in html, f"ui:html:{token}"))
    for endpoint in (
        "/api/v1/dashboard/overview",
        "/api/v1/dashboard/strategies",
        "/api/v1/dashboard/performance",
        "/api/v1/dashboard/alerts",
        "/api/v1/dashboard/audit",
        "/api/v1/dashboard/reset-counters",
    ):
        results.append(_check(endpoint in js, f"ui:js:{endpoint}"))
    return results


def main() -> int:
    sha, branch = _git_info()
    timestamp = datetime.now(timezone.utc).isoformat()
    report_lines: List[str] = []
    report_lines.append(f"Stage 9 Audit Report (9.1-9.4)")
    report_lines.append(f"Timestamp: {timestamp}")
    report_lines.append(f"Git: {sha} ({branch})")
    report_lines.append("")

    checks: List[Dict[str, Any]] = []
    checks.extend(_static_checks())
    with tempfile.TemporaryDirectory() as tmp:
        app = _build_test_app(Path(tmp))
        checks.extend(_api_checks(app))
        checks.extend(_alert_checks(app))
        checks.extend(_performance_checks(app))
    checks.extend(_ui_checks())

    report_lines.append("Stage status:")
    stage1 = all(item["ok"] for item in checks if item["label"].startswith("api:") or item["label"].startswith("docs:") or item["label"].startswith("exists:"))
    stage2 = all(item["ok"] for item in checks if item["label"].startswith("alerts:"))
    stage3 = all(item["ok"] for item in checks if item["label"].startswith("ui:"))
    stage4 = all(item["ok"] for item in checks if item["label"].startswith("perf:") or item["label"].startswith("stage9.4:"))
    report_lines.append(f"- 9.1 Dashboard backend: {'PASS' if stage1 else 'FAIL'}")
    report_lines.append(f"- 9.2 Alerts engine: {'PASS' if stage2 else 'FAIL'}")
    report_lines.append(f"- 9.3 Dashboard UI: {'PASS' if stage3 else 'FAIL'}")
    report_lines.append(f"- 9.4 Performance stats: {'PASS' if stage4 else 'FAIL'}")
    report_lines.append("")
    report_lines.append("Checklist:")
    for item in checks:
        status = "PASS" if item["ok"] else "FAIL"
        details = f" - {item['details']}" if item.get("details") else ""
        report_lines.append(f"- {status}: {item['label']}{details}")

    failed = [item for item in checks if not item["ok"]]
    report_lines.append("")
    report_lines.append(f"Overall: {'PASS' if not failed else 'FAIL'}")
    report_lines.append(f"Failures: {len(failed)}")

    report_path = SUPERVISOR_DIR / "artifacts" / "audit_stage9_1_9_4_report.txt"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(report_lines), encoding="utf-8")
    print(f"Report written: {report_path}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
