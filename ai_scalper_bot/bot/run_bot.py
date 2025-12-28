import asyncio
import time
import logging
import json
import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Dict, Any

import yaml

from bot.ai.risk_moderator import LLMRiskModerator
from bot.core.config_loader import config, load_supervisor_settings, load_supervisor_snapshot_settings
from bot.engine.decision_engine import DecisionEngine
from bot.engine.decision_types import DecisionAction
from bot.market_data.mock_ws_manager import MockWSManager
from bot.market_data.ws_manager import WSManager
from bot.ml.ensemble import EnsembleOutput
from bot.ml.signal_model.model import SignalOutput
from bot.ml.signal_model.online_features import OnlineFeatureBuilder
from bot.ml.runtime_models import resolve_models_root
from bot.ml.runtime.policy_loader import load_policy, load_policy_override, resolve_policy_path
from bot.ml.features.builder import schema_hash as ml_schema_hash, schema_version as ml_schema_version, feature_names as ml_feature_names
from bot.ml.drift_monitor import DriftMonitor
from bot.ml.runtime.model_manager import ModelManager
from bot.ml.runtime.aggregator import MultiHorizonAggregator, build_ensemble_output
from bot.trading.executor import BinanceDemoExecutor
from bot.trading.bingx_executor import BingXDemoExecutor
from bot.trading.paper_trader import PaperTrader
from bot.trading.execution_mode import NormalExecutionMode, ScalpExecutionMode
from bot.trading.order_policy import OrderPolicy
from bot.trading.trade_stats import TradeStats
from bot.market_data.data_manager import DataManager
from bot.supervisor_client import SupervisorClient, SupervisorClientConfig
from bot.integrations.supervisor_snapshot_client import SupervisorSnapshotClient
from bot.monitoring.supervisor_snapshot_monitor import run_supervisor_snapshot_monitor
from bot.risk.scalp_guards import ScalpGuard
from bot.ops.status_writer import BotStatusWriter
from bot.policy.policy_client import PolicyClient
from bot.policy.policy_gate import policy_allows_entry
from telemetry.emitter import TelemetryEmitter, TelemetryConfig

_kill_cache = {"ts": 0.0, "active": False, "reason": None}


def _kill_switch_active() -> Dict[str, Any]:
    """Check kill switch file/config with light caching."""
    now = time.time()
    if now - _kill_cache["ts"] < 2.0:
        return _kill_cache
    cfg = config.get("risk", {}) or {}
    ks_cfg = cfg.get("kill_switch", {}) or {}
    file_path = Path(ks_cfg.get("file", "state/kill_switch.json"))
    active = bool(ks_cfg.get("enabled", False))
    reason = ks_cfg.get("reason")
    if file_path.exists():
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                active = bool(data.get("enabled", active))
                reason = data.get("reason") or reason
        except Exception:
            pass
    _kill_cache.update({"ts": now, "active": active, "reason": reason})
    return _kill_cache



def _resolve_data_source() -> str:
    websocket_cfg = config.get("app.websocket", {})
    if isinstance(websocket_cfg, dict):
        return "ws" if websocket_cfg.get("enabled", False) else "mock"
    if isinstance(websocket_cfg, str):
        val = websocket_cfg.lower()
        if val in {"mock", "disabled", "off"}:
            return "mock"
        return "ws"
    return "mock"


def _parse_ml_thresholds(ml_cfg: Dict[str, Any], horizons: list[int]) -> Dict[int, float]:
    raw = ml_cfg.get("thresholds") or {}
    thresholds: Dict[int, float] = {}
    default = None
    if isinstance(raw, dict):
        if "p_up" in raw:
            try:
                default = float(raw.get("p_up"))
            except (TypeError, ValueError):
                default = None
        for key, value in raw.items():
            if key in {"p_up", "default"}:
                continue
            if key.startswith("h"):
                key = key[1:]
            try:
                horizon = int(key)
                thresholds[horizon] = float(value)
            except (TypeError, ValueError):
                continue
    if not thresholds and default is not None:
        thresholds = {int(h): float(default) for h in horizons}
    return thresholds


def _parse_ml_weights(ml_cfg: Dict[str, Any], horizons: list[int]) -> Dict[int, float]:
    raw = ml_cfg.get("weights") or {}
    weights: Dict[int, float] = {int(h): 1.0 for h in horizons}
    if isinstance(raw, dict):
        for key, value in raw.items():
            key_str = str(key)
            if key_str.startswith("h"):
                key_str = key_str[1:]
            try:
                horizon = int(key_str)
                weights[horizon] = float(value)
            except (TypeError, ValueError):
                continue
    return weights


def _model_error_reasons(errors: Dict[int, str]) -> List[str]:
    reasons: List[str] = []
    for horizon, reason in errors.items():
        if reason in {"SCHEMA_HASH_MISMATCH", "FEATURE_NAMES_MISMATCH"}:
            reasons.append(reason)
        elif reason.startswith("MODEL") or "MISSING" in reason:
            reasons.append(f"MODEL_MISSING_H{horizon}")
        else:
            reasons.append(f"MODEL_INVALID_H{horizon}")
    return reasons


def _shadow_skip(shadow_mode: bool, decision) -> bool:
    if not shadow_mode or decision is None:
        return False
    return decision.action in {DecisionAction.ENTER, DecisionAction.EXIT}


async def _event_stream(symbols):
    data_source = _resolve_data_source()
    if data_source == "ws":
        try:
            print("[INFO] Market data source: websocket (WSManager).")
            ws = WSManager()
            async for event in ws.stream():
                try:
                    price = float(event.get("p", 0))
                    event.setdefault("b", float(event.get("b", price)))
                    event.setdefault("a", float(event.get("a", price)))
                    event.setdefault("depth", float(event.get("depth", 0.0) or 0.0))
                except Exception:
                    pass
                yield event
        except Exception as exc:
            print(f"[WARN] Websocket source failed ({exc}); falling back to mock.")

    print("[INFO] Market data source: mock websocket.")
    mock = MockWSManager(symbols)
    async for event in mock.stream():
        # add minimal depth hints for scalp mode
        try:
            price = float(event.get("p"))
            qty = float(event.get("q"))
            event.setdefault("b", price)
            event.setdefault("a", price)
            event.setdefault("depth", price * qty)
        except Exception:
            pass
        yield event


def _build_signal_from_meta(meta: EnsembleOutput) -> SignalOutput:
    p_up = 0.5 + meta.meta_edge
    p_down = 0.5 - meta.meta_edge
    return SignalOutput(p_up=p_up, p_down=p_down, edge=meta.meta_edge, direction=meta.direction)


async def main(stop_event: Optional[asyncio.Event] = None, once: bool = False, status_writer: Optional[BotStatusWriter] = None, logger: Optional[logging.Logger] = None):
    if logger is None:
        logging.basicConfig(level=getattr(logging, str(config.get("app.log_level", "INFO")).upper(), logging.INFO))
    supervisor_cfg = load_supervisor_settings(config)
    print(f"[INFO] Using config: {config.config_path}")
    print(f"[INFO] Supervisor URL: {supervisor_cfg.base_url}")
    policy_cfg = config.get("policy", {}) or {}
    policy_source = str(policy_cfg.get("policy_source", "auto")).lower()
    policy_file_raw = policy_cfg.get("policy_file_path", "runtime/policy.json")
    policy_file_path = Path(policy_file_raw)
    if not policy_file_path.is_absolute():
        policy_file_path = (Path(config.qe_root) / policy_file_path).resolve()
    policy_api_url = str(policy_cfg.get("policy_api_url", "http://127.0.0.1:8765/api/v1/policy/current"))
    policy_ttl_grace_sec = int(policy_cfg.get("policy_ttl_grace_sec", 0) or 0)
    safe_mode_default = str(policy_cfg.get("safe_mode_default", "risk_off"))
    policy_client = PolicyClient(
        source=policy_source,
        file_path=policy_file_path,
        api_url=policy_api_url,
        ttl_grace_sec=policy_ttl_grace_sec,
        safe_mode_default=safe_mode_default,
        request_timeout_s=float(policy_cfg.get("policy_api_timeout_s", 0.3)),
    )
    telemetry_cfg = config.get("telemetry", {}) or {}
    telemetry_enabled = bool(telemetry_cfg.get("enabled", True))
    telemetry_sink = str(telemetry_cfg.get("sink", "http")).lower()
    telemetry_http_url = str(telemetry_cfg.get("http_url", "http://127.0.0.1:8765/api/v1/telemetry/ingest"))
    telemetry_file = Path(telemetry_cfg.get("file_path", "runtime/telemetry.jsonl"))
    if not telemetry_file.is_absolute():
        telemetry_file = (Path(config.qe_root) / telemetry_file).resolve()
    telemetry_flush = float(telemetry_cfg.get("flush_interval_sec", 1))
    telemetry_queue = int(telemetry_cfg.get("max_queue", 1000))
    telemetry_timeout = float(telemetry_cfg.get("http_timeout_s", 0.3))
    telemetry_max_kb = int(telemetry_cfg.get("max_event_size_kb", 32))
    sample_cfg = telemetry_cfg.get("sample", {}) or {}
    latency_every_n = int(sample_cfg.get("latency_every_n", 10) or 10)
    telemetry_emitter = TelemetryEmitter(
        TelemetryConfig(
            enabled=telemetry_enabled,
            sink=telemetry_sink,
            http_url=telemetry_http_url,
            file_path=telemetry_file,
            flush_interval_sec=telemetry_flush,
            max_queue=telemetry_queue,
            timeout_s=telemetry_timeout,
            max_event_kb=telemetry_max_kb,
        )
    ) if telemetry_enabled else None

    def emit_event(event_type: str, data: Dict[str, Any], symbol_override: Optional[str] = None) -> None:
        if telemetry_emitter:
            telemetry_emitter.emit_event(event_type, data, symbol_override)
    mode = str(config.get("app.mode", "paper")).lower()
    demo_mode = mode == "demo"
    data_source = _resolve_data_source()
    start_time = time.time()
    ml_cfg = config.get("ml", {}) or {}
    require_models = bool(config.get("ml.require_models", True))
    ml_enabled = bool(ml_cfg.get("enabled", True))
    ml_required = bool(ml_cfg.get("ml_required", ml_cfg.get("required", require_models)))
    ml_compat_strict = bool(ml_cfg.get("ml_compat_strict", False))
    ml_fail_mode = str(ml_cfg.get("fail_mode", "disable")).lower()
    ml_snapshot_interval = float(ml_cfg.get("snapshot_interval_sec", 30))
    if ml_snapshot_interval <= 0:
        ml_snapshot_interval = 30.0
    ml_horizons = list(ml_cfg.get("horizons", [1, 5, 15]))
    ml_thresholds_cfg = _parse_ml_thresholds(ml_cfg, ml_horizons)
    ml_weights_cfg = _parse_ml_weights(ml_cfg, ml_horizons)
    ml_policy_type = str(ml_cfg.get("policy", "and_gate")).lower()
    ml_score_threshold = float(ml_cfg.get("score_threshold", 0.0))
    ml_min_gap = float(ml_cfg.get("min_confidence_gap", 0.0))
    ml_cooldown_ms = int(ml_cfg.get("cooldown_ms", 0))
    policy_path = resolve_policy_path(ml_cfg, Path(config.qe_root))
    if policy_path:
        raw_policy = load_policy(policy_path)
        if not raw_policy:
            print(f"[WARN] ML policy path set but could not load: {policy_path}")
        elif raw_policy.schema_hash and raw_policy.schema_hash != ml_schema_hash():
            print(f"[WARN] ML policy schema mismatch; ignoring policy at {policy_path}")
        else:
            policy_override = load_policy_override(ml_cfg, Path(config.qe_root))
            if policy_override:
                if policy_override.horizons:
                    ml_horizons = policy_override.horizons
                if policy_override.thresholds:
                    ml_thresholds_cfg = policy_override.thresholds
                if policy_override.weights:
                    ml_weights_cfg = policy_override.weights
                if policy_override.policy_type:
                    ml_policy_type = str(policy_override.policy_type).lower()
                if policy_override.score_threshold is not None:
                    ml_score_threshold = float(policy_override.score_threshold)
                if not policy_override.weights:
                    ml_weights_cfg = _parse_ml_weights(ml_cfg, ml_horizons)
                print(f"[INFO] ML policy override loaded: {policy_override.path}")
    ml_feature_list = ml_feature_names()
    observer_mode = not require_models
    observer_notice_logged = False
    missing_required_models = False
    missing_models_notice_logged = False

    if status_writer is None:
        ops_cfg = config.get("ops", {}) or {}
        status_file = Path(ops_cfg.get("status_file", "state/bot_status.json"))
        write_interval = float(ops_cfg.get("write_interval_seconds", 2))
        status_writer = BotStatusWriter(status_file, interval_seconds=write_interval)

    exchange_name = str(os.getenv("EXCHANGE") or config.get("app.exchange") or config.get("exchange") or "binance_demo").lower()
    demo_cfg_key = "bingx_demo" if exchange_name == "bingx_swap" else "binance_demo"
    demo_cfg = config.get(demo_cfg_key, {}) or {}
    if exchange_name == "bingx_swap" and not demo_cfg:
        demo_cfg = config.get("binance_demo", {}) or {}

    if demo_mode and not demo_cfg.get("enabled", True):
        print(f"[ERROR] Demo mode requested but {demo_cfg_key}.enabled is false. Aborting start.")
        return

    symbol_candidates = demo_cfg.get("symbols", []) if demo_mode else config.get("binance.symbols", [])
    symbols = symbol_candidates or ["BTCUSDT"]
    if demo_mode and exchange_name == "bingx_swap":
        symbols = [str(s).replace("-", "").upper() for s in symbols if s]
    pairs_allowed = set()
    try:
        pairs_data = yaml.safe_load(Path("config/pairs.yaml").read_text()) or {}
        pairs_allowed = set(str(s).upper() for s in pairs_data.get("futures_demo", []) if s)
    except Exception:
        pass
    if demo_mode and pairs_allowed:
        filtered = []
        for s in symbols:
            if s.upper() in pairs_allowed:
                filtered.append(s)
            else:
                print(f"[WARN] Symbol {s} not in pairs.yaml:futures_demo; skipping.")
        symbols = filtered or symbols
    symbol = symbols[0]

    print(f"[INFO] App mode: {mode}")
    if demo_mode:
        print(
            f"[INFO] Demo executor wired to {exchange_name}. "
            f"Symbol={symbol}, max_notional_per_trade={demo_cfg.get('max_notional_per_trade', 50)}"
        )
    app_risk = config.get("app.risk", {}) or {}
    min_edge = app_risk.get("llm_require_edge", 0.0)
    data_manager = DataManager()

    execution_cfg = config.get("execution", {}) or {}
    exec_mode_name = str(execution_cfg.get("mode", "normal")).lower()
    scalp_cfg = execution_cfg.get("scalp", {}) or {}
    shadow_mode = bool(execution_cfg.get("shadow", False))
    scalp_enabled = exec_mode_name == "scalp" and bool(scalp_cfg.get("enabled", False))
    if scalp_enabled and data_source != "ws":
        print("[WARN] Disabling scalp mode because live depth is not available (data_source != ws).")
        scalp_enabled = False
    circuit_cfg = (config.get("decision", {}) or {}).get("circuit_breakers", {}) or {}
    max_daily_loss = float(circuit_cfg.get("max_daily_loss", 0.0) or 0.0)
    max_drawdown_abs = float(circuit_cfg.get("max_drawdown_abs", 0.0) or 0.0)
    scalp_guard: Optional[ScalpGuard] = None
    order_policy: Optional[OrderPolicy] = None
    if scalp_enabled:
        rg = scalp_cfg.get("risk_guards", {}) or {}
        scalp_guard = ScalpGuard(
            max_positions=int(rg.get("max_open_scalp_positions", 2)),
            max_trades=int(rg.get("max_daily_scalp_trades", 200)),
            max_loss_pct=float(rg.get("max_daily_scalp_loss_pct", 3.0)),
        )
        order_policy = OrderPolicy(scalp_cfg.get("order_policy", {}) or {}, logging.getLogger("order_policy"))
    if scalp_enabled and data_source != "ws":
        print("[WARN] Scalp mode enabled without live depth; using mock/estimated depth only.")

    model_source = str(ml_cfg.get("model_source", "runtime")).lower()
    models_root = None
    if model_source == "artifacts":
        models_root = Path(ml_cfg.get("models_root", Path(config.qe_root) / "artifacts" / "models"))
    else:
        models_root = Path(ml_cfg.get("runtime_models_dir", resolve_models_root()))
    if not models_root.is_absolute():
        models_root = (Path(config.qe_root) / models_root).resolve()
    reload_interval = float(ml_cfg.get("reload_interval_sec", 30))
    engines = {}
    if observer_mode and not observer_notice_logged:
        print("[WARN] Observer mode enabled (ml.require_models=false); trading disabled.")
        observer_notice_logged = True
    for sym in symbols:
        model_versions = {}
        feature_stats = {}
        drift_monitor = None

        model_manager = ModelManager(
            symbol=sym,
            horizons=ml_horizons,
            models_root=models_root,
            source=model_source,
            reload_interval_s=reload_interval,
        )
        model_manager.load()
        if model_manager.errors:
            print(f"[WARN] Model manager errors for {sym}: {model_manager.errors}")

        thresholds = dict(ml_thresholds_cfg) if ml_thresholds_cfg else model_manager.thresholds()
        weights = dict(ml_weights_cfg) if ml_weights_cfg else {h: 1.0 for h in ml_horizons}
        aggregator = MultiHorizonAggregator(
            policy=ml_policy_type,
            thresholds=thresholds,
            weights=weights,
            score_threshold=ml_score_threshold,
            min_gap=ml_min_gap,
            cooldown_ms=ml_cooldown_ms,
        )

        model_versions = dict(model_manager.model_versions)
        feature_stats = dict(model_manager.feature_stats or {})

        drift_cfg = (ml_cfg.get("drift") or {})
        drift_window = int(drift_cfg.get("window", 300))
        drift_z = float(drift_cfg.get("z_threshold", 3.0))
        if feature_stats.get("mean") and feature_stats.get("std"):
            drift_monitor = DriftMonitor(
                baseline_mean=feature_stats.get("mean") or {},
                baseline_std=feature_stats.get("std") or {},
                window=drift_window,
                z_threshold=drift_z,
            )
        if model_manager.errors and ml_required:
            print(f"[ERROR] Required models missing for {sym}; errors={model_manager.errors}")
            missing_required_models = True
            continue
        if model_manager.errors and not missing_models_notice_logged:
            print(f"[WARN] Models missing/invalid for {sym}; entries will be blocked. errors={model_manager.errors}")
            missing_models_notice_logged = True
        warmup = config.get("ml.warmup_seconds", 600)
        feature_builder = OnlineFeatureBuilder(warmup_seconds=warmup)
        engine = DecisionEngine()
        if demo_mode:
            trader = BingXDemoExecutor(symbol=sym) if exchange_name == "bingx_swap" else BinanceDemoExecutor(symbol=sym)
            ok = await trader.healthcheck()
            if not ok:
                print(f"[ERROR] Demo mode healthcheck failed for {sym}. Verify demo API keys and connectivity.")
                continue
            if demo_cfg.get("healthcheck_only", False):
                print("[INFO] Healthcheck-only flag set; exiting after connectivity test.")
                return
        elif mode == "paper":
            trader = PaperTrader()
            print("[INFO] Using PaperTrader (paper mode).")
        else:
            raise ValueError(f"Unknown app mode: {mode}")
        execution_mode = (
            ScalpExecutionMode(scalp_cfg, scalp_guard, order_policy, logging.getLogger(f"execution.{sym}"))
            if scalp_enabled and scalp_guard and order_policy
            else NormalExecutionMode(logging.getLogger(f"execution.{sym}"))
        )
        stats_obj = TradeStats()
        if hasattr(trader, "trade_stats"):
            trader.trade_stats = stats_obj
        engine.trade_stats[sym] = stats_obj
        engines[sym] = {
            "model_manager": model_manager,
            "aggregator": aggregator,
            "ml_weights": weights,
            "feature_builder": feature_builder,
            "engine": engine,
            "trader": trader,
            "risk_mod": LLMRiskModerator(),
            "llm_enabled": bool(config.get("app.llm_enabled", True)),
            "execution_mode": execution_mode,
            "trading_enabled": not observer_mode,
            "last_realized": 0.0,
            "circuit_pause_until": 0.0,
            "circuit_reason": None,
            "depth_warned": False,
            "ml_enabled": ml_enabled,
            "ml_fail_mode": ml_fail_mode,
            "ml_thresholds": thresholds,
            "ml_policy": ml_policy_type,
            "ml_state": {
                "sum_p_up": {h: 0.0 for h in ml_horizons},
                "count": {h: 0 for h in ml_horizons},
                "blocked": 0,
                "last_snapshot": time.time(),
            },
            "ml_versions": model_versions,
            "ml_gate_counts": {},
            "last_trade_ms": None,
            "drift_monitor": drift_monitor,
            "last_entry": None,
        }

    if not engines:
        if missing_required_models and require_models:
            print("[ERROR] No engines initialized because models are missing and ml.require_models=true.")
            return 1
        print("[ERROR] No engines initialized; exiting.")
        return 1
    else:
        for sym, ctx in engines.items():
            mgr = ctx.get("model_manager")
            loaded = sorted(list(mgr.entries.keys())) if mgr else []
            errors = mgr.errors if mgr else {}
            print(f"[INFO] Model readiness [{sym}]: horizons loaded={loaded or 'none'} errors={errors}")

    sup_settings = load_supervisor_settings(config)
    supervisor_client: Optional[SupervisorClient] = None
    snapshot_client: Optional[SupervisorSnapshotClient] = None
    snapshot_monitor_task: Optional[asyncio.Task] = None
    if sup_settings.enabled:
        sup_cfg = SupervisorClientConfig(
            base_url=sup_settings.base_url,
            api_token=sup_settings.api_token,
            timeout_s=sup_settings.timeout_s,
            heartbeat_interval_s=sup_settings.heartbeat_interval_s,
            on_error=sup_settings.on_error,
            risk_enabled=sup_settings.risk_enabled,
            risk_on_error=sup_settings.risk_on_error,
            risk_log_level=sup_settings.risk_log_level,
        )
        supervisor_client = SupervisorClient(sup_cfg, logging.getLogger("supervisor_client"))
        print(f"[INFO] SupervisorAgent heartbeat enabled to {sup_cfg.base_url}")

    snapshot_settings = load_supervisor_snapshot_settings(config)
    if snapshot_settings.enabled:
        snapshot_client = SupervisorSnapshotClient(snapshot_settings, logging.getLogger("supervisor_snapshot_client"))
        snapshot_monitor_task = asyncio.create_task(
            run_supervisor_snapshot_monitor(snapshot_settings, snapshot_client, logging.getLogger("supervisor_snapshot_monitor"))
        )
        print(f"[INFO] Supervisor snapshot monitor enabled -> {snapshot_settings.supervisor_url}{snapshot_settings.endpoint}")

    last_report = time.time()
    report_interval = 5.0
    last_policy_snapshot: Dict[str, Any] = {}

    def write_status(extra: Optional[Dict[str, Any]] = None) -> None:
        if not status_writer:
            return
        try:
            summary_any = next(iter(engines.values()))
        except StopIteration:
            summary_any = None
        trader_summary = summary_any["trader"].summary() if summary_any else {"position": 0.0, "trades": 0}
        status_payload = {
            "ts": datetime.utcnow().replace(tzinfo=timezone.utc).isoformat(),
            "is_running": True,
            "is_trading": False if observer_mode else bool(trader_summary.get("position")),
            "open_positions": sum(1 for ctx in engines.values() if ctx["trader"].position),
            "open_orders": 0,
            "mode": mode,
            "data_source": data_source,
            "last_error": None,
            "info": "running",
        }
        if extra:
            status_payload.update(extra)
        status_writer.update(status_payload)
        emit_event(
            "status",
            {
                "state": "RUNNING" if status_payload.get("is_running") else "STOPPED",
                "uptime_sec": time.time() - start_time,
                "mode": mode,
            },
            symbol,
        )

    try:
        write_status({"info": "startup"})
        emit_event("status", {"state": "STARTING", "uptime_sec": 0.0, "mode": mode}, symbol)
        latency_counter = 0
        async for event in _event_stream(list(engines.keys())):
            loop_start = time.perf_counter()
            if stop_event and stop_event.is_set():
                break
            try:
                ts = int(event.get("E") or event.get("T") or time.time() * 1000)
                price = float(event["p"])
                qty = float(event["q"])
                evt_symbol = event.get("s", symbol)
            except Exception:
                emit_event("error", {"code": "event_parse", "message": "Failed to parse market event", "where": "event_stream"}, symbol)
                continue
            if evt_symbol not in engines:
                continue

            await data_manager.save_trade(event)

            ctx = engines[evt_symbol]
            feature_builder = ctx["feature_builder"]
            model_manager = ctx["model_manager"]
            aggregator = ctx["aggregator"]
            ml_weights = ctx.get("ml_weights", {})
            engine = ctx["engine"]
            trader = ctx["trader"]
            risk_mod = ctx["risk_mod"]
            llm_enabled = ctx["llm_enabled"]
            trading_enabled = ctx["trading_enabled"]
            circuit_pause_until = ctx.get("circuit_pause_until", 0.0)
            circuit_reason = ctx.get("circuit_reason")
            now_s = time.time()
            skip_trading = bool(circuit_pause_until and now_s < circuit_pause_until)
            if circuit_pause_until and now_s >= circuit_pause_until:
                ctx["circuit_pause_until"] = 0.0
                ctx["circuit_reason"] = None
            kill = _kill_switch_active()
            if kill.get("active"):
                skip_trading = True
                ctx["circuit_reason"] = kill.get("reason") or "kill_switch"

            if hasattr(trader, "check_brackets"):
                try:
                    result = await trader.check_brackets(price, ts)
                except TypeError:
                    result = trader.check_brackets(price, ts)
                except Exception:
                    result = False
                if result:
                    continue

            if scalp_enabled and data_source == "ws":
                depth_missing = event.get("b") is None or event.get("a") is None or (event.get("depth") in (None, 0))
                if depth_missing:
                    skip_trading = True
                    if not ctx.get("depth_warned"):
                        print(f"[WARN] Depth info unavailable for {evt_symbol}; scalp decisions paused until depth is received.")
                        ctx["depth_warned"] = True

            side = "sell" if event.get("m") else "buy"
            features = feature_builder.add_tick(ts, price, qty, side=side)
            meta = EnsembleOutput(meta_edge=0.0, direction=0, components={})
            pseudo_signal = None
            if features is None:
                skip_trading = True
            else:
                block, _ = EnsembleSignalModel.filter_blocks(features)
                if block:
                    skip_trading = True
                else:
                    model_manager.maybe_reload()
                    prediction = model_manager.predict(features)
                    meta = build_ensemble_output(prediction.outputs, ml_weights)
                    pseudo_signal = _build_signal_from_meta(meta)
                    if telemetry_emitter:
                        emit_event(
                            "ml_prediction",
                            {
                                "schema_hash": ml_schema_hash(),
                                "latency_ms": prediction.latency_ms,
                                "probs": {h: {"p_up": sig.p_up, "p_down": sig.p_down} for h, sig in prediction.outputs.items()},
                                "model_versions": ctx.get("ml_versions", {}),
                            },
                            evt_symbol,
                        )
                    if (model_manager.errors or not prediction.outputs) and not ctx.get("ml_gate_warned"):
                        reasons = _model_error_reasons(model_manager.errors) if model_manager.errors else ["MODEL_MISSING"]
                        if telemetry_emitter:
                            emit_event(
                                "ml_gate",
                                {
                                    "policy": ctx.get("ml_policy"),
                                    "allow": False,
                                    "reasons": reasons,
                                    "thresholds": ctx.get("ml_thresholds"),
                                    "probs": {},
                                },
                                evt_symbol,
                            )
                        ctx["ml_gate_warned"] = True
                    ml_state = ctx.get("ml_state")
                    if isinstance(ml_state, dict):
                        for h, sig in meta.components.items():
                            if h in ml_state.get("sum_p_up", {}):
                                ml_state["sum_p_up"][h] += float(sig.p_up)
                                ml_state["count"][h] += 1
                        drift_monitor = ctx.get("drift_monitor")
                        if drift_monitor:
                            drift_monitor.update(ml_feature_list, list(features))

            approved = True
            if trading_enabled and not skip_trading and llm_enabled and pseudo_signal is not None:
                shock = abs(float(features[0]))
                market_context = {
                    "drawdown": 0.0,  # placeholder for real equity curve tracking
                    "exposure": abs(trader.position),
                    "shock": shock,
                }
                try:
                    verdict = await risk_mod.evaluate(features, pseudo_signal, market_context)
                    approved = verdict.get("approve", True)
                except Exception as exc:
                    approved = True
                    print(f"[WARN] LLM risk moderator failed; falling back to rules. err={exc}")
                    emit_event("error", {"code": "llm_risk_moderator", "message": str(exc), "where": "risk_moderator"}, evt_symbol)

            policy = policy_client.get_effective_policy()
            policy_snapshot = {"mode": policy.mode, "allow_trading": policy.allow_trading, "reason": policy.reason}
            if policy_snapshot != last_policy_snapshot:
                emit_event("policy", policy_snapshot, evt_symbol)
                last_policy_snapshot = policy_snapshot

            position_state = 1 if trader.position > 0 else (-1 if trader.position < 0 else 0)
            if trading_enabled and not skip_trading:
                decision = engine.decide(
                    symbol=evt_symbol,
                    ensemble=meta,
                    features=features,
                    position=position_state,
                    approved=approved,
                    warmup_ready=True,
                )
                if decision.action == DecisionAction.ENTER and not policy_allows_entry(decision.action, policy):
                    logging.getLogger("policy_client").info(
                        "Policy blocks entry for %s (mode=%s allow_trading=%s reason=%s)",
                        evt_symbol,
                        policy.mode,
                        policy.allow_trading,
                        policy.reason,
                    )
                    decision = None
                elif decision.action == DecisionAction.ENTER:
                    ml_state = ctx.get("ml_state", {})
                    ml_gate_enabled = bool(ctx.get("ml_enabled", True))
                    model_errors = model_manager.errors if model_manager else {}
                    error_reasons = _model_error_reasons(model_errors) if model_errors else []
                    gate = aggregator.evaluate(
                        meta.components,
                        decision.direction,
                        ts,
                        ctx.get("last_trade_ms"),
                    ) if ml_gate_enabled else None
                    if error_reasons and gate is None:
                        gate = aggregator.evaluate({}, decision.direction, ts, ctx.get("last_trade_ms"))
                        gate.reasons = error_reasons
                        gate.allow = False
                    elif gate and error_reasons:
                        gate.reasons = error_reasons + gate.reasons
                        gate.allow = False
                    if gate and not gate.allow:
                        if not ctx.get("threshold_warned"):
                            print(f"[WARN] ML gate blocked entry for {evt_symbol}: {gate.reasons}")
                            ctx["threshold_warned"] = True
                        ml_state["blocked"] = int(ml_state.get("blocked", 0)) + 1
                        counts = ctx.get("ml_gate_counts", {})
                        for reason in gate.reasons or ["ML_GATE_BLOCK"]:
                            counts[reason] = int(counts.get(reason, 0)) + 1
                        ctx["ml_gate_counts"] = counts
                        if telemetry_emitter:
                            emit_event(
                                "ml_gate",
                                {
                                    "policy": ctx.get("ml_policy"),
                                    "allow": False,
                                    "reasons": gate.reasons,
                                    "thresholds": ctx.get("ml_thresholds"),
                                    "probs": gate.raw,
                                },
                                evt_symbol,
                            )
                        decision = None
                    elif gate and telemetry_emitter:
                        emit_event(
                            "ml_gate",
                            {
                                "policy": ctx.get("ml_policy"),
                                "allow": True,
                                "reasons": [],
                                "thresholds": ctx.get("ml_thresholds"),
                                "probs": gate.raw,
                            },
                            evt_symbol,
                        )
                elif decision.action == DecisionAction.ENTER and policy.size_multiplier != 1.0:
                    decision.size = max(0.0, decision.size * policy.size_multiplier)
                if decision and decision.action not in (DecisionAction.NO_TRADE, DecisionAction.HOLD):
                    # Map decision to existing trader actions
                    execution_mode = ctx["execution_mode"]
                    if decision.action == DecisionAction.ENTER and isinstance(execution_mode, ScalpExecutionMode):
                        gate_info = execution_mode.evaluate_entry(
                            decision,
                            price,
                            evt_symbol,
                            signal=pseudo_signal,
                            last_event=event,
                        )
                        if telemetry_emitter:
                            emit_event(
                                "scalp_gate",
                                {
                                    "allow": bool(gate_info.get("ok")),
                                    "reason": gate_info.get("reason"),
                                    "spread_bps": gate_info.get("spread_bps"),
                                    "depth_usd": gate_info.get("depth_usd"),
                                },
                                evt_symbol,
                            )
                        if not gate_info.get("ok"):
                            decision = None
                    if _shadow_skip(shadow_mode, decision):
                        if telemetry_emitter:
                            emit_event(
                                "execution_intent",
                                {
                                    "shadow": True,
                                    "action": decision.action,
                                    "direction": decision.direction,
                                    "size": decision.size,
                                    "order_type": decision.order_type,
                                },
                                evt_symbol,
                            )
                        decision = None
                        # Skip execution but continue telemetry/monitoring.
                    if decision:
                        async def _supervisor_allows(action: str, size: float, reduce_only: bool) -> bool:
                            if supervisor_client is None:
                                return True
                            size_val = float(size or 0.0)
                            side = "BUY"
                            if action == "sell":
                                side = "SELL"
                            elif action == "close":
                                side = "SELL" if trader.position > 0 else "BUY"
                            payload = {
                                "symbol": evt_symbol,
                                "side": side,
                                "order_type": "MARKET",
                                "quantity": size_val,
                                "price": float(price),
                                "notional": float(price * size_val) if size_val else None,
                                "leverage": None,
                                "is_reduce_only": bool(reduce_only),
                            }
                            decision_resp = await supervisor_client.evaluate_order(payload)
                            if decision_resp is None:
                                return True
                            if decision_resp.get("allowed", False):
                                return True
                            logging.getLogger("supervisor_client").warning(
                                "Order blocked by Supervisor: code=%s reason=%s",
                                decision_resp.get("code"),
                                decision_resp.get("reason"),
                            )
                            return False

                        if telemetry_emitter:
                            emit_event(
                                "execution_intent",
                                {
                                    "shadow": False,
                                    "action": decision.action,
                                    "direction": decision.direction,
                                    "size": decision.size,
                                    "order_type": decision.order_type,
                                },
                                evt_symbol,
                            )
                        result = await execution_mode.execute_trade(
                            decision,
                            price,
                            ts,
                            evt_symbol,
                            trader,
                            allow_fn=_supervisor_allows,
                            signal=pseudo_signal,
                            last_event=event,
                        )
                        if result.skipped and result.reason not in {"noop"}:
                            logging.getLogger("execution").debug("Execution skipped (%s)", result.reason)
                        if result.executed:
                            emit_event(
                                "order",
                                {
                                    "side": result.action,
                                    "qty": result.size,
                                    "price": price,
                                    "order_type": "market",
                                    "client_order_id": None,
                                },
                                evt_symbol,
                            )
                            emit_event(
                                "fill",
                                {
                                    "side": result.action,
                                    "qty": result.size,
                                    "price": price,
                                    "fee": None,
                                    "order_id": None,
                                },
                                evt_symbol,
                            )
                            if result.action in {"buy", "sell"}:
                                ctx["last_entry"] = {
                                    "entry_ts": ts,
                                    "entry_price": price,
                                    "side": result.action,
                                    "qty": result.size,
                                    "probs": {h: {"p_up": sig.p_up, "p_down": sig.p_down} for h, sig in meta.components.items()},
                                }
                            elif result.action == "close":
                                entry = ctx.pop("last_entry", None)
                                if entry and supervisor_client is not None:
                                    direction = 1 if entry.get("side") == "buy" else -1
                                    pnl = (price - entry.get("entry_price", price)) * float(entry.get("qty", 0.0)) * direction
                                    payload = {
                                        "symbol": evt_symbol,
                                        "side": entry.get("side"),
                                        "qty": entry.get("qty"),
                                        "entry_ts": entry.get("entry_ts"),
                                        "exit_ts": ts,
                                        "entry_price": entry.get("entry_price"),
                                        "exit_price": price,
                                        "pnl_realized": pnl,
                                        "fees": None,
                                        "reason_close": result.reason,
                                        "ml_probs": entry.get("probs"),
                                    }
                                    await supervisor_client.send_trade_result(payload)

                        if telemetry_emitter:
                            emit_event(
                                "execution_result",
                                {
                                    "executed": bool(result.executed),
                                    "reason": result.reason,
                                    "action": result.action,
                                    "size": result.size,
                                    "skipped": result.skipped,
                                },
                                evt_symbol,
                            )
                        if result.executed and result.action in {"buy", "sell"}:
                            ctx["last_trade_ms"] = ts

                        # Time-based exit for scalp mode (no-op for normal)
                        await execution_mode.enforce_time_stop(trader, price, ts, evt_symbol, allow_fn=_supervisor_allows)

            ml_state = ctx.get("ml_state") if isinstance(ctx, dict) else None
            if telemetry_emitter and ml_state:
                now_snap = time.time()
                last_snap = float(ml_state.get("last_snapshot", 0.0) or 0.0)
                if now_snap - last_snap >= ml_snapshot_interval:
                    avg_p_up = {}
                    for h in ml_horizons:
                        count = int(ml_state.get("count", {}).get(h, 0))
                        total = float(ml_state.get("sum_p_up", {}).get(h, 0.0))
                        avg_p_up[h] = (total / count) if count else None
                    drift_monitor = ctx.get("drift_monitor")
                    drift_snapshot = drift_monitor.snapshot() if drift_monitor else None
                    emit_event(
                        "ml_snapshot",
                        {
                            "schema_version": ml_schema_version(),
                            "model_versions": ctx.get("ml_versions", {}),
                            "avg_p_up": avg_p_up,
                            "blocked_entries": int(ml_state.get("blocked", 0)),
                            "drift": {
                                "score": drift_snapshot.drift_score,
                                "exceed_rate": drift_snapshot.exceed_rate,
                                "top_features": drift_snapshot.top_features,
                            } if drift_snapshot else None,
                        },
                        evt_symbol,
                    )
                    ml_state["sum_p_up"] = {h: 0.0 for h in ml_horizons}
                    ml_state["count"] = {h: 0 for h in ml_horizons}
                    ml_state["blocked"] = 0
                    ml_state["last_snapshot"] = now_snap

            stats = engine.trade_stats.setdefault(evt_symbol, TradeStats())
            now = time.time()
            if now - last_report >= report_interval:
                summary = trader.summary()
                print(
                    f"[STATS][{evt_symbol}] pos={summary['position']:.2f} trades={summary['trades']} "
                    f"pnl={summary['realized_pnl'] + summary['open_pnl']:.4f} meta_edge={meta.meta_edge:.4f}"
                )
                emit_event(
                    "pnl",
                    {
                        "equity": float(summary.get("realized_pnl", 0.0) + summary.get("open_pnl", 0.0)),
                        "pnl_day": float(stats.total_pnl()),
                        "drawdown_day": float(stats.max_drawdown_abs()),
                    },
                    evt_symbol,
                )
                last_report = now

            if supervisor_client is not None:
                def build_payload() -> dict:
                    summary = trader.summary()
                    open_notional = abs(summary.get("position", 0.0) * price)
                    equity_start = float(demo_cfg.get("equity_override", 0) or 0)
                    realized = float(summary.get("realized_pnl", 0.0))
                    unrealized = float(summary.get("open_pnl", 0.0))
                    equity = equity_start + realized + unrealized
                    last_tick_iso = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat()
                    return {
                        "mode": mode,
                        "uptime_s": time.time() - start_time,
                        "equity": equity,
                        "realized_pnl_today": realized,
                        "unrealized_pnl": unrealized,
                        "open_positions": 1 if summary.get("position") else 0,
                        "open_notional": open_notional,
                        "last_tick_ts": last_tick_iso,
                        "base_currency": "USDT",
                    }

                await supervisor_client.send_heartbeat_if_due(build_payload)

            # trade stats + circuit breakers
            losses = stats.loss_streak(engine.loss_cfg.get("window_trades", 10), engine.loss_cfg.get("max_losses", 3))
            overtrading = stats.trades_last_hour() >= engine.over_cfg.get("max_trades_per_hour", 60)
            daily_pnl = stats.total_pnl()
            dd_abs = stats.max_drawdown_abs()
            triggered = None
            if losses >= engine.loss_cfg.get("max_losses", 3):
                triggered = "loss_streak"
            if overtrading:
                triggered = triggered or "max_trades_per_hour"
            if max_daily_loss > 0 and daily_pnl <= -abs(max_daily_loss):
                triggered = triggered or "max_daily_loss"
            if max_drawdown_abs > 0 and dd_abs >= max_drawdown_abs:
                triggered = triggered or "max_drawdown_abs"

            if triggered and not ctx.get("circuit_pause_until"):
                cooldown = engine.loss_cfg.get("cooldown_seconds", 600)
                ctx["circuit_pause_until"] = time.time() + cooldown
                ctx["circuit_reason"] = triggered
                print(f"[WARN] Circuit breaker triggered for {evt_symbol}: {triggered}; cooldown {cooldown}s")

              status_extra = {
                  "circuit_paused": bool(ctx.get("circuit_pause_until", 0) and time.time() < ctx.get("circuit_pause_until", 0)),
                  "circuit_reason": ctx.get("circuit_reason"),
                  "trades_last_hour": stats.trades_last_hour(),
                  "total_pnl": stats.total_pnl(),
                  "max_drawdown_abs": stats.max_drawdown_abs(),
                  "risk_block": engine.last_risk_state.get(evt_symbol, ""),
                  "kill_switch": bool(_kill_switch_active().get("active")),
                  "kill_reason": _kill_switch_active().get("reason"),
                  "policy_mode": policy.mode,
                  "policy_allow_trading": policy.allow_trading,
                  "policy_reason": policy.reason,
                  "ml_policy": ctx.get("ml_policy"),
                  "ml_gate_counts": ctx.get("ml_gate_counts"),
                  "shadow_mode": shadow_mode,
              }

            write_status(status_extra)

            if once:
                break
            latency_counter += 1
            if telemetry_emitter and latency_every_n > 0 and latency_counter % latency_every_n == 0:
                loop_ms = (time.perf_counter() - loop_start) * 1000.0
                emit_event("latency", {"loop_ms": loop_ms}, evt_symbol)
            await asyncio.sleep(0.02)
    finally:
        if snapshot_monitor_task:
            snapshot_monitor_task.cancel()
            try:
                await snapshot_monitor_task
            except asyncio.CancelledError:
                pass
        try:
            data_manager.close()
        except Exception:
            pass
        write_status({"is_running": False})


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Shutting down.")
