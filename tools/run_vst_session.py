#!/usr/bin/env python3
"""Persistent Single-Process RAM-First BingX VST Session Runner (Phase 9 Live Console).

Holds a single long-lived instance of:
  - AgyDirectCliTransport (default, zero HTTP proxy, fail-soft --resume) or agy-proxy / mock
  - PrefixContextAssembler + RAM SnapshotStore (stable prefix_hash across cycles)
  - SQLite WAL ThesisMemoryStore + UsageLedger + OrderLedger
  - LocalForecastProvider (in-RAM CausalHMM + EGARCH + LightGBM + ACI)
  - ConvictionPositionSizer + ReasoningEffortRouter + DeterministicTradingOrchestrator
  - Background 5-second PositionProtectionWatcher (0 LLM tokens, scoped to --symbols)
  - Real-time foreground console dashboard + graceful Ctrl+C + Markdown report export
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from quantum_edge_core.contracts.lean import TradeDecision
from quantum_edge_core.contracts.optimization import (
    RequestStatus,
    TransportChannel,
    UsageRecordV1,
)
from quantum_edge_core.execution.bingx_vst_adapter import (
    cancel_vst_open_orders,
    create_bingx_vst_client,
    fetch_vst_status,
)
from quantum_edge_core.execution.harness_bridge import (
    HarnessBridge,
    PaperExchangeClient,
)
from quantum_edge_core.execution.lean_executor import LeanExecutor
from quantum_edge_core.execution.market_context_serializer import MarketContextSerializer
from quantum_edge_core.execution.order_ledger import OrderLedger
from quantum_edge_core.execution.position_protection import PositionProtectionWatcher
from quantum_edge_core.execution.position_sizer import ConvictionPositionSizer
from quantum_edge_core.execution.risk_guard import (
    QUANTUMEDGE_DEFAULT_UNIVERSE,
    LeanRiskGuard,
    RiskConfig,
)
from quantum_edge_core.execution.storage_policy import (
    CANONICAL_SSD_DURABLE_PATHS,
    get_ram_tmp_dir,
)
from quantum_edge_core.orchestrator.deterministic_orchestrator import (
    DeterministicTradingOrchestrator,
    LocalForecastProvider,
    PreFetchCoordinator,
    scope_vst_status_to_symbols,
)
from quantum_edge_core.orchestrator.prefix_context_assembler import (
    PrefixContextAssembler,
    SnapshotStore,
    ThesisMemoryStore,
)
from quantum_edge_ml.model_router.backends.transport import (
    AgyDirectCliTransport,
    AgyProxyTransport,
    ImageAttachment,
    LLMRequest,
    LLMResponse,
    LLMTransport,
)
from quantum_edge_ml.model_router.reasoning_router import ReasoningEffortRouter
from quantum_edge_ml.model_router.usage_ledger import UsageLedger
from tools.qe_harness_cli import (
    DEFAULT_JOURNAL_PATH,
    DEFAULT_LEDGER_DB,
    DEFAULT_USAGE_LEDGER_DB,
    _build_offline_mock_bundle,
)


class _SessionMockTransport(LLMTransport):
    """Deterministic in-memory mock transport for multi-cycle persistent session tests."""

    def __init__(self, ledger: UsageLedger, default_symbol: str = "BNBUSDT") -> None:
        super().__init__(ledger)
        self.default_symbol = default_symbol
        self.calls: int = 0

    async def generate(self, req: LLMRequest) -> LLMResponse:
        self.calls += 1
        rec_id = f"sessmock-{uuid.uuid4().hex[:10]}"
        in_chars = len(req.system_instruction) + len(req.combined_user_text)
        txt = json.dumps(
            {
                "market_regime_summary": f"{self.default_symbol} persistent session mock hold.",
                "action": "HOLD",
                "candidate_symbol": self.default_symbol,
                "hold_blocker_code": "NO_LIQ_ASYMMETRY",
                "candidate_rationale": "Session mock fast Stage-1 HOLD.",
                "estimated_edge_bps": 8.0,
            }
        )
        usage = UsageRecordV1(
            record_id=rec_id,
            decision_id=req.decision_id,
            stage=req.stage,
            transport=TransportChannel.AGY_CLI_PROXY,
            model_provider_id=req.model,
            agy_alias=f"{req.model}-low",
            thinking_level=req.thinking_level,
            request_status=RequestStatus.OK,
            provider_input_tokens=max(1, in_chars // 4),
            cache_read_tokens=0,
            output_tokens=40,
            thinking_tokens=80,
            total_tokens=max(1, in_chars // 4) + 120,
            input_chars=in_chars,
            output_chars=len(txt),
            latency_ms=4.0,
            timeout_ms=req.timeout_sec * 1000.0,
            prefix_hash=req.prefix_hash,
            context_hash=req.context_hash,
            snapshot_id=req.snapshot_id,
        )
        self.ledger.record_usage(usage)
        return LLMResponse(text=txt, usage=usage)

    async def count_tokens(
        self, model: str, text_parts: list[str], images: list[ImageAttachment] | None = None
    ) -> dict[str, int]:
        return {"text_tokens": 0, "image_tokens_media_res": 0, "total_tokens": 0}

    async def close(self) -> None:
        return None


def _derive_regime_signature(forecasts: dict[str, Any]) -> str:
    if not forecasts:
        return "STEADY"
    tags: list[str] = []
    for sym in sorted(forecasts.keys()):
        fc = forecasts[sym]
        rp = fc.regime_probs if hasattr(fc, "regime_probs") else (fc.get("regime_probs") or {})
        dom = max(rp.items(), key=lambda kv: float(kv[1]))[0] if rp else "RANGE"
        tags.append(f"{sym}:{dom}")
    return ",".join(tags)


def _format_tf_indicator_snippet(serializer: MarketContextSerializer, ctx: dict[str, Any], tf: str) -> str:
    bars = ((ctx or {}).get("ohlcv_by_tf") or {}).get(tf) or []
    if not bars:
        return f"{tf}:N/A"
    p = serializer.build_structural_profile(tf, bars)
    return (
        f"{tf}[adx={p['adx14']},di={p['di_diff']},rsi={p['rsi14']},"
        f"mc={p['macd_cross']},div={p['rsi_div']}/{p['macd_div']}]"
    )


def _render_session_markdown(summary: dict[str, Any]) -> str:
    u = summary.get("usage_summary") or {}
    lines = [
        "# QuantumEdge Persistent VST Session Report",
        "",
        "## 1. Session Summary",
        f"- **Started UTC:** `{summary.get('started_utc')}`",
        f"- **Finished UTC:** `{summary.get('finished_utc')}`",
        f"- **Mode:** `{'DRY_RUN' if summary.get('dry_run') else 'LIVE_VST'}`",
        f"- **Transport / Model:** `{summary.get('transport')}` / `{summary.get('model')}` (reuse_session=`{summary.get('reuse_session')}`)",
        f"- **Universe (1d/4h + 1h/15m/5m):** `{','.join(summary.get('symbols') or [])}`",
        f"- **Start Equity (VST):** `{summary.get('start_equity_vst')}` -> **End Equity (VST):** `{summary.get('end_equity_vst')}`",
        f"- **Cycles Executed:** `{summary.get('cycles_count')}` (cycle_errors=`{summary.get('cycle_errors_count', 0)}`, quota_stopped=`{summary.get('quota_stopped', False)}`) | **Prefix Hash Stable:** `{summary.get('prefix_hash_stable')}` (`{summary.get('unique_prefix_hashes')}`)",
        f"- **PositionProtectionWatcher Runs:** `{summary.get('watcher_runs')}`",
        "",
        "## 2. UsageLedger Telemetry",
        f"- Total Requests: `{u.get('total_requests', 0)}` (failures=`{u.get('failure_rate', 0.0)}`)",
        f"- Provider Input Tokens: `{u.get('sum_provider_input_tokens', 0)}` | Cache Read Tokens: `{u.get('sum_cache_read_tokens', 0)}`",
        f"- Output Tokens: `{u.get('sum_output_tokens', 0)}` | Thinking Tokens: `{u.get('sum_thinking_tokens', 0)}`",
        f"- Sum IECU: `{u.get('sum_iecu', 0.0)}` | Latency p95 (ms): `{u.get('latency_imputed_p95_ms')}`",
        "",
        "## 3. Cycle Log",
        "| # | Decision ID | Resync | Prefix Hash | Action | Symbol | Thinking | Validator | Exec Status |",
        "|---:|---|---|---|---|---|---|---|---|",
    ]
    for c in summary.get("cycles") or []:
        lines.append(
            f"| {c.get('cycle')} | `{c.get('decision_id')}` | `{c.get('resync_mode')}` | `{c.get('prefix_hash')}` | "
            f"`{c.get('action')}` | `{c.get('symbol')}` | `{c.get('thinking_level')}` | `{c.get('validator_passed')}` | `{c.get('execution_status')}` |"
        )
    return "\n".join(lines) + "\n"


async def run_persistent_session(
    args: argparse.Namespace,
    *,
    transport_override: LLMTransport | None = None,
    exchange_client_override: Any | None = None,
) -> dict[str, Any]:
    """Execute a persistent RAM-First multi-cycle VST session in a single Python process."""
    started_utc = datetime.now(timezone.utc).isoformat()
    symbols = tuple(s.strip().upper() for s in str(args.symbols).split(",") if s.strip())
    dry_run = not bool(args.live_vst)
    reuse_session = bool(getattr(args, "reuse_session", True) or getattr(args, "reuse_agy_session", False))

    usage_ledger = UsageLedger(args.ledger_db)
    order_ledger = OrderLedger(args.order_ledger_db)
    risk_cfg = RiskConfig.for_qe_isolated_50x(
        symbols=symbols,
        max_margin_vst=args.max_margin_vst,
        max_leverage=args.max_leverage,
        max_open_positions=args.max_open_positions,
        max_notional_per_pair_vst=args.max_notional_vst,
    )
    risk_guard = LeanRiskGuard(risk_cfg)

    thesis_db_path = args.thesis_db
    if thesis_db_path is None:
        if args.transport == "mock" and dry_run:
            thesis_db_path = get_ram_tmp_dir("sessions") / f"mock_thesis_{uuid.uuid4().hex[:6]}.sqlite3"
        else:
            thesis_db_path = CANONICAL_SSD_DURABLE_PATHS["thesis_memory"]
    thesis_store = ThesisMemoryStore(thesis_db_path)

    if exchange_client_override is not None:
        ex_client = exchange_client_override
    elif not dry_run:
        ex_client = create_bingx_vst_client()
    else:
        class _SessionOfflinePaperClient(PaperExchangeClient):
            _qe_sandbox_verified = True

            def fetch_balance(self) -> dict[str, Any]:
                return {"VST": {"free": 10000.0, "total": 10000.0, "used": 0.0}}

            def fetch_positions(self) -> list[dict[str, Any]]:
                return []

            def fetch_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
                return []

        ex_client = _SessionOfflinePaperClient()

    if transport_override is not None:
        transport: LLMTransport = transport_override
    elif args.transport == "mock":
        transport = _SessionMockTransport(
            usage_ledger, default_symbol=symbols[0] if symbols else "BNBUSDT"
        )
    elif args.transport == "agy-proxy":
        transport = AgyProxyTransport(usage_ledger, base_url=args.proxy_url, record_to_ledger=True)
    else:
        transport = AgyDirectCliTransport(
            usage_ledger,
            reuse_session=reuse_session,
            record_to_ledger=True,
        )

    executor = LeanExecutor(ex_client, order_ledger, risk_guard)
    bridge = HarnessBridge(executor, journal_path=args.journal_path)
    snapshot_store = SnapshotStore()
    serializer = MarketContextSerializer()
    assembler = PrefixContextAssembler(
        serializer=serializer,
        thesis_store=thesis_store,
        snapshot_store=snapshot_store,
        include_images=False,
        risk_limits=risk_cfg,
    )
    forecast_provider = LocalForecastProvider(refit_interval_sec=900.0)
    effort_router = ReasoningEffortRouter(model_id=args.model)
    position_sizer = ConvictionPositionSizer()
    orchestrator = DeterministicTradingOrchestrator(
        transport=transport,
        ledger=usage_ledger,
        assembler=assembler,
        bridge=bridge,
        effort_router=effort_router,
        position_sizer=position_sizer,
        model_id=args.model,
        stage1_timeout_sec=args.stage1_timeout,
        stage2_timeout_sec=args.stage2_timeout,
    )
    watcher = PositionProtectionWatcher(
        ex_client,
        bridge,
        allowed_symbols=symbols,
        interval_sec=float(args.protection_interval_sec),
        enable_breakeven_sl=bool(getattr(args, "enable_breakeven_sl", True)),
        breakeven_trigger_pct=float(getattr(args, "be_trigger_pct", 0.40)),
        breakeven_offset_pct=float(getattr(args, "be_offset_pct", 0.12)),
        dry_run=dry_run,
    )
    watcher_runs = 0
    watcher_actions: list[str] = []

    stop_requested = asyncio.Event()
    old_handlers: dict[int, Any] = {}
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            old_handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, lambda _s, _f: stop_requested.set())
        except (ValueError, RuntimeError):
            pass

    init_status = await asyncio.to_thread(fetch_vst_status, ex_client, fallback_symbols=symbols)
    start_equity_vst = (init_status.get("balance_vst") or {}).get("total")
    print(
        f"[START] QuantumEdge VST Session | mode={'DRY_RUN' if dry_run else 'LIVE_VST'} "
        f"| transport={args.transport} | model={args.model} | reuse_session={reuse_session} "
        f"| symbols={','.join(symbols)} | TFs=1d/4h(P1)+1h/15m/5m(P2) "
        f"| cap={args.max_margin_vst} VST x {args.max_leverage}x | start_equity={start_equity_vst} VST",
        flush=True,
    )

    async def _watcher_loop() -> None:
        nonlocal watcher_runs
        while not watcher._stop.is_set():
            try:
                res = await watcher.check_once()
                watcher_runs += 1
                watcher_actions.extend(res.get("actions") or [])
            except Exception:
                pass
            try:
                await asyncio.wait_for(watcher._stop.wait(), timeout=watcher.interval_sec)
            except asyncio.TimeoutError:
                continue

    watcher_task = asyncio.create_task(_watcher_loop())
    cycles: list[dict[str, Any]] = []
    duration_sec = max(0.0, float(args.duration_minutes) * 60.0)
    t_start_mono = time.monotonic()
    deadline = t_start_mono + duration_sec
    prefetch = PreFetchCoordinator(
        ex_client,
        symbols=symbols,
        risk_config=risk_cfg,
        include_btc_leader=bool(getattr(args, "include_btc_leader", True)),
    )
    seq = 0
    cycle_errors_count = 0
    quota_stopped = False
    unhandled_crash = False
    exit_cleanup: dict[str, Any] = {
        "executed": False,
        "crash_preserved": False,
        "closed_symbols": [],
        "cancelled_orders": {},
    }

    try:
        while not stop_requested.is_set():
            seq += 1
            cycle_action_cursor = len(watcher_actions)
            try:
                if not dry_run or exchange_client_override is not None:
                    bundle = await prefetch.fetch_all(include_analytics=True)
                else:
                    bundle = _build_offline_mock_bundle(symbols)
                watcher.touch_feed_heartbeat()

                forecasts = forecast_provider.build_forecasts(bundle)
                regime_sig = _derive_regime_signature(forecasts)
                audit = await orchestrator.run_decision_cycle(
                    bundle,
                    trigger_summary=f"SESSION_CYCLE_{seq}",
                    forecasts_by_symbol=forecasts or None,
                    dry_run=dry_run,
                    current_seq=seq,
                    regime_signature=regime_sig,
                )
                resync_mode_val = (
                    assembler.last_resync_mode.value
                    if assembler.last_resync_mode is not None
                    else "UNKNOWN"
                )
                if resync_mode_val == "RESYNC" and hasattr(transport, "reset_session"):
                    transport.reset_session()

                dp = audit.decision_payload
                exec_res = audit.execution_result or {}
                sizing = dp.get("sizing")
                usage_snap = usage_ledger.summarize()

                elapsed_s = round(time.monotonic() - t_start_mono, 1)
                now_utc_str = datetime.now(timezone.utc).strftime("%H:%M:%SZ")
                print(
                    f"[{now_utc_str}] Cycle #{seq} ({elapsed_s}s/{round(duration_sec, 1)}s) "
                    f"| resync={resync_mode_val} | prefix_hash={audit.prefix_hash}",
                    flush=True,
                )
                ml = bundle.market_leader
                if isinstance(ml, dict) and ml.get("px") is not None:
                    print(
                        f"  BTC_LDR  | px={ml.get('px')} 15m={ml.get('chg_15m_bps')}bps "
                        f"1h={ml.get('chg_1h_bps')}bps 4h={ml.get('chg_4h_bps')}bps "
                        f"rsi15m={ml.get('rsi14_15m')} mc15m={ml.get('macd_15m')} trend={ml.get('trend')}",
                        flush=True,
                    )
                for sym in sorted(bundle.contexts_by_symbol.keys()):
                    ctx = bundle.contexts_by_symbol[sym]
                    snap = ctx.get("market_snapshot") or {}
                    s_1d = _format_tf_indicator_snippet(serializer, ctx, "1d")
                    s_4h = _format_tf_indicator_snippet(serializer, ctx, "4h")
                    fc = forecasts.get(sym)
                    fc_txt = (
                        f"U={fc.regime_probs.get('UP', 0):.2f}/R={fc.regime_probs.get('RANGE', 0):.2f}/D={fc.regime_probs.get('DOWN', 0):.2f}"
                        if fc is not None
                        else "N/A"
                    )
                    print(
                        f"  PAIR     | {sym}: px={snap.get('last_price')} spr={snap.get('spread_bps')}bps "
                        f"| {s_1d} {s_4h} | FC:{fc_txt}",
                        flush=True,
                    )
                print(
                    f"  DECISION | action={dp.get('action')} sym={dp.get('symbol')} type={dp.get('order_type')} "
                    f"entry={dp.get('entry_price')} sl={dp.get('sl_price')} tp={dp.get('tp_price')} "
                    f"| think={audit.thinking_level.value} val={audit.validator_passed} ({audit.validator_reason})",
                    flush=True,
                )
                print(
                    f"  THESIS   | reg={dp.get('regime_read') or ''} | edge={dp.get('primary_edge') or ''} "
                    f"| ctr={dp.get('counter_thesis') or ''}",
                    flush=True,
                )
                if isinstance(sizing, dict):
                    print(
                        f"  SIZING   | ConvictionPositionSizer risk={sizing.get('risk_score')} conv={sizing.get('conviction')} "
                        f"| notional={sizing.get('notional_vst')} VST | margin={sizing.get('margin_vst')} x {sizing.get('leverage')}x "
                        f"| est_sl_loss={sizing.get('est_loss_at_sl_vst')} VST",
                        flush=True,
                    )
                pre_scoped = scope_vst_status_to_symbols(bundle.vst_status, symbols)
                if not dry_run and exec_res.get("status") not in (None, "SKIPPED_HOLD", "REJECTED"):
                    raw_post = await asyncio.to_thread(
                        fetch_vst_status, ex_client, fallback_symbols=symbols
                    )
                    post_scoped = scope_vst_status_to_symbols(raw_post, symbols)
                else:
                    post_scoped = pre_scoped
                upnl_sum = round(
                    sum(float(p.get("unrealized_pnl") or 0.0) for p in post_scoped.get("open_positions") or []),
                    4,
                )
                recent_prot = watcher_actions[cycle_action_cursor:]
                prot_suffix = f" | prot={','.join(recent_prot)}" if recent_prot else ""
                print(
                    f"  EXEC     | status={exec_res.get('status')} order_id={exec_res.get('exchange_order_id')} "
                    f"| pre_pos={pre_scoped.get('open_positions_count', 0)}/post_pos={post_scoped.get('open_positions_count', 0)} "
                    f"pre_ord={pre_scoped.get('open_orders_count', 0)}/post_ord={post_scoped.get('open_orders_count', 0)} "
                    f"uPnL={upnl_sum}{prot_suffix}",
                    flush=True,
                )
                print(
                    f"  LEDGER   | UsageLedger reqs={usage_snap.get('total_requests', 0)} fail={usage_snap.get('failure_rate', 0.0)} "
                    f"| in={usage_snap.get('sum_provider_input_tokens', 0)} cache={usage_snap.get('sum_cache_read_tokens', 0)} "
                    f"out={usage_snap.get('sum_output_tokens', 0)} think={usage_snap.get('sum_thinking_tokens', 0)} "
                    f"| IECU={usage_snap.get('sum_iecu', 0.0)} p95={usage_snap.get('latency_imputed_p95_ms')}ms",
                    flush=True,
                )

                cycles.append(
                    {
                        "cycle": seq,
                        "decision_id": audit.decision_id,
                        "snapshot_id": audit.snapshot_id,
                        "prefix_hash": audit.prefix_hash,
                        "context_hash": audit.context_hash,
                        "resync_mode": resync_mode_val,
                        "action": dp.get("action"),
                        "symbol": dp.get("symbol"),
                        "thinking_level": audit.thinking_level.value,
                        "complexity_score": audit.complexity_score,
                        "validator_passed": audit.validator_passed,
                        "validator_reason": audit.validator_reason,
                        "execution_status": exec_res.get("status"),
                        "sizing": sizing,
                        "forecast_symbols": sorted(forecasts.keys()),
                    }
                )
            except Exception as cycle_exc:
                cycle_errors_count += 1
                is_quota = AgyDirectCliTransport._is_quota_error(cycle_exc)
                print(
                    f"[ERROR] Cycle #{seq} failed (quota={is_quota}): {cycle_exc}",
                    flush=True,
                )
                if hasattr(transport, "reset_session"):
                    transport.reset_session()
                cycles.append(
                    {
                        "cycle": seq,
                        "decision_id": None,
                        "snapshot_id": None,
                        "prefix_hash": "",
                        "context_hash": "",
                        "resync_mode": "ERROR",
                        "action": "CYCLE_ERROR",
                        "symbol": None,
                        "thinking_level": "N/A",
                        "complexity_score": None,
                        "validator_passed": False,
                        "validator_reason": str(cycle_exc)[:180],
                        "execution_status": "QUOTA_REACHED" if is_quota else "ERROR",
                        "sizing": None,
                        "forecast_symbols": [],
                    }
                )
                if is_quota:
                    quota_stopped = True
                    stop_requested.set()
                    break

            remaining = deadline - time.monotonic()
            if remaining <= 0.0 or stop_requested.is_set():
                break
            sleep_for = min(float(args.cycle_interval_sec), remaining)
            if sleep_for <= 0.0:
                break
            try:
                await asyncio.wait_for(stop_requested.wait(), timeout=sleep_for)
            except asyncio.TimeoutError:
                pass
            if stop_requested.is_set():
                break
            if time.monotonic() >= deadline and len(cycles) >= 1:
                if len(cycles) == 1 and float(args.duration_minutes) > 0:
                    continue
                break
    except KeyboardInterrupt:
        stop_requested.set()
    except BaseException:
        unhandled_crash = True
        raise
    finally:
        for sig, prev in old_handlers.items():
            with contextlib.suppress(Exception):
                signal.signal(sig, prev)

        watcher.stop()
        try:
            await asyncio.wait_for(watcher_task, timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            watcher_task.cancel()
            with contextlib.suppress(Exception):
                await watcher_task

        should_close_on_exit = (
            not dry_run
            and bool(args.close_on_exit)
            and (not unhandled_crash or bool(getattr(args, "close_on_crash", False)))
        )
        if not dry_run and bool(args.close_on_exit) and not should_close_on_exit:
            exit_cleanup["crash_preserved"] = True
            print(
                "[WARN] Crash exit: leaving positions & exchange SL/TP untouched (pass --close-on-crash to force close).",
                flush=True,
            )

        if should_close_on_exit:
            exit_cleanup["executed"] = True
            raw_status = await asyncio.to_thread(
                fetch_vst_status, ex_client, fallback_symbols=symbols
            )
            scoped = scope_vst_status_to_symbols(raw_status, symbols)
            for pos in scoped.get("open_positions") or []:
                sym = str(pos.get("symbol") or "").upper()
                side_raw = str(pos.get("positionSide") or pos.get("side") or "").upper()
                qty = float(pos.get("contracts") or 0.0)
                if sym in symbols and qty > 0:
                    close_action = "CLOSE_LONG" if "LONG" in side_raw else "CLOSE_SHORT"
                    mark = float(pos.get("mark_price") or pos.get("entry_price") or 0.0)
                    dec = TradeDecision.model_validate(
                        {
                            "decision_id": f"sess-exit-{sym.lower()}-{uuid.uuid4().hex[:6]}",
                            "symbol": sym,
                            "action": close_action,
                            "order_type": "MARKET",
                            "qty": qty,
                            "reason": "Persistent session close_on_exit cleanup",
                        }
                    )
                    await asyncio.to_thread(
                        bridge.submit_decision,
                        dec,
                        current_price=mark,
                        open_positions_count=int(scoped.get("open_positions_count") or 0),
                        position_qty=qty,
                    )
                    exit_cleanup["closed_symbols"].append(sym)
            for sym in symbols:
                c_res = await asyncio.to_thread(cancel_vst_open_orders, ex_client, sym)
                exit_cleanup["cancelled_orders"][sym] = c_res.get("cancelled_count", 0)

        end_status = await asyncio.to_thread(fetch_vst_status, ex_client, fallback_symbols=symbols)
        end_equity_vst = (end_status.get("balance_vst") or {}).get("total")
        usage_summary = usage_ledger.summarize()
        await transport.close()
        thesis_store.close()
        usage_ledger.close()

    prefix_hashes = [c["prefix_hash"] for c in cycles if c.get("prefix_hash")]
    prefix_hash_stable = len(set(prefix_hashes)) <= 1 if prefix_hashes else False

    ram_path = (
        Path(args.session_summary_ram)
        if args.session_summary_ram is not None
        else get_ram_tmp_dir("sessions") / f"session_{int(time.time())}_{uuid.uuid4().hex[:6]}.json"
    )
    ram_path.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "started_utc": started_utc,
        "finished_utc": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "transport": args.transport,
        "model": args.model,
        "reuse_session": reuse_session,
        "symbols": list(symbols),
        "start_equity_vst": start_equity_vst,
        "end_equity_vst": end_equity_vst,
        "cycles_count": len(cycles),
        "cycle_errors_count": cycle_errors_count,
        "quota_stopped": quota_stopped,
        "unhandled_crash": unhandled_crash,
        "prefix_hash_stable": prefix_hash_stable,
        "unique_prefix_hashes": sorted(set(prefix_hashes)),
        "cycles": cycles,
        "watcher_runs": watcher_runs,
        "watcher_actions": watcher_actions,
        "exit_cleanup": exit_cleanup,
        "usage_summary": usage_summary,
        "ram_summary_path": str(ram_path),
    }
    ram_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    report_out = getattr(args, "report_out", None)
    if report_out is not None:
        r_path = Path(report_out)
        r_path.parent.mkdir(parents=True, exist_ok=True)
        r_path.write_text(_render_session_markdown(summary), encoding="utf-8")
        summary["report_out"] = str(r_path)
        if getattr(args, "sync_gdrive", False):
            sync_script = ROOT / "tools" / "sync_report_to_gdrive.sh"
            if sync_script.is_file():
                subprocess.run([str(sync_script), str(r_path)], check=False)

    print(
        f"[END] Session finished | cycles={len(cycles)} | prefix_hash_stable={prefix_hash_stable} "
        f"| end_equity={end_equity_vst} VST | IECU={usage_summary.get('sum_iecu', 0.0)}",
        flush=True,
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_vst_session",
        description="Persistent RAM-First single-process BingX VST Session Runner",
    )
    mode_grp = parser.add_mutually_exclusive_group()
    mode_grp.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Run offline/paper session without exchange mutations (default)",
    )
    mode_grp.add_argument(
        "--live-vst",
        action="store_true",
        default=False,
        help="Execute against BingX VST sandbox",
    )
    parser.add_argument("--duration-minutes", type=float, default=15.0, help="Session duration in minutes (default: 15.0)")
    parser.add_argument("--cycle-interval-sec", type=float, default=180.0, help="Seconds between decision cycles (default: 180.0)")
    parser.add_argument("--protection-interval-sec", type=float, default=5.0, help="PositionProtectionWatcher interval in seconds (default: 5.0)")
    parser.add_argument("--symbols", default=",".join(QUANTUMEDGE_DEFAULT_UNIVERSE), help="Comma-separated symbols (default: BNBUSDT,LINKUSDT,SUIUSDT)")
    parser.add_argument("--model", default="gemini-4-carbon", help="Canonical Gemini 4 model (default: gemini-4-carbon)")
    parser.add_argument("--transport", choices=("agy-direct", "agy-proxy", "mock"), default="agy-direct", help="LLM transport (default: agy-direct)")
    parser.add_argument("--proxy-url", default="http://127.0.0.1:8045/v1", help="Proxy URL if --transport agy-proxy")
    reuse_grp = parser.add_mutually_exclusive_group()
    reuse_grp.add_argument("--reuse-session", dest="reuse_session", action="store_true", default=True, help="Enable fail-soft --resume chaining in AgyDirectCliTransport (default)")
    reuse_grp.add_argument("--no-reuse-session", dest="reuse_session", action="store_false", help="Disable --resume chaining in AgyDirectCliTransport")
    parser.add_argument("--reuse-agy-session", action="store_true", default=False, help=argparse.SUPPRESS)
    parser.add_argument("--max-margin-vst", type=float, default=1000.0)
    parser.add_argument("--max-leverage", type=int, default=50)
    parser.add_argument("--max-open-positions", type=int, default=3)
    parser.add_argument("--max-notional-vst", type=float, default=50_000.0)
    parser.add_argument("--stage1-timeout", type=float, default=90.0)
    parser.add_argument("--stage2-timeout", type=float, default=150.0)
    parser.add_argument("--be-trigger-pct", type=float, default=0.40, help="Breakeven SL trigger move pct (default: 0.40)")
    parser.add_argument("--be-offset-pct", type=float, default=0.12, help="Breakeven SL lock offset pct (default: 0.12)")
    parser.add_argument("--no-breakeven-sl", dest="enable_breakeven_sl", action="store_false", default=True, help="Disable deterministic breakeven SL move in watcher")
    parser.add_argument("--no-btc-leader", dest="include_btc_leader", action="store_false", default=True, help="Disable read-only BTC_LEADER pulse in PreFetchCoordinator")
    exit_grp = parser.add_mutually_exclusive_group()
    exit_grp.add_argument("--close-on-exit", dest="close_on_exit", action="store_true", default=True, help="Close scoped positions & cancel orders on clean exit/SIGINT/SIGTERM (default)")
    exit_grp.add_argument("--keep-positions-on-exit", dest="close_on_exit", action="store_false", help="Leave scoped positions open on exit")
    parser.add_argument("--close-on-crash", action="store_true", default=False, help="Allow --close-on-exit even on unhandled exception crash")
    parser.add_argument("--ledger-db", type=Path, default=DEFAULT_USAGE_LEDGER_DB)
    parser.add_argument("--order-ledger-db", type=Path, default=DEFAULT_LEDGER_DB)
    parser.add_argument("--thesis-db", type=Path, default=None)
    parser.add_argument("--journal-path", type=Path, default=DEFAULT_JOURNAL_PATH)
    parser.add_argument("--session-summary-ram", type=Path, default=None, help="Output JSON path in /dev/shm/quantumedge/sessions")
    parser.add_argument("--report-out", type=Path, default=None, help="Optional Markdown report output path")
    parser.add_argument("--sync-gdrive", action="store_true", default=False, help="Sync --report-out to Google Drive after session completion")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = asyncio.run(run_persistent_session(args))
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
