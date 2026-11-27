#!/usr/bin/env python3
"""Persistent Single-Process RAM-First BingX VST Session Runner (Phase 8).

Holds a single long-lived instance of:
  - AgyDirectCliTransport (default, zero HTTP proxy) or agy-proxy / mock
  - PrefixContextAssembler + RAM SnapshotStore (stable prefix_hash across cycles)
  - SQLite WAL ThesisMemoryStore + UsageLedger + OrderLedger
  - LocalForecastProvider (in-RAM CausalHMM + EGARCH + LightGBM + ACI)
  - ConvictionPositionSizer + ReasoningEffortRouter + DeterministicTradingOrchestrator
  - Background 5-second PositionProtectionWatcher (0 LLM tokens, scoped to --symbols)
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
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
            reuse_session=bool(getattr(args, "reuse_agy_session", False)),
            record_to_ledger=True,
        )

    executor = LeanExecutor(ex_client, order_ledger, risk_guard)
    bridge = HarnessBridge(executor, journal_path=args.journal_path)
    snapshot_store = SnapshotStore()
    assembler = PrefixContextAssembler(
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
        dry_run=dry_run,
    )
    watcher_runs = 0
    watcher_actions: list[str] = []

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
    deadline = time.monotonic() + duration_sec
    prefetch = PreFetchCoordinator(ex_client, symbols=symbols, risk_config=risk_cfg)
    seq = 0
    exit_cleanup: dict[str, Any] = {"executed": False, "closed_symbols": [], "cancelled_orders": {}}

    try:
        while True:
            seq += 1
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
            cycles.append(
                {
                    "cycle": seq,
                    "decision_id": audit.decision_id,
                    "snapshot_id": audit.snapshot_id,
                    "prefix_hash": audit.prefix_hash,
                    "context_hash": audit.context_hash,
                    "resync_mode": resync_mode_val,
                    "action": audit.decision_payload.get("action"),
                    "symbol": audit.decision_payload.get("symbol"),
                    "thinking_level": audit.thinking_level.value,
                    "complexity_score": audit.complexity_score,
                    "validator_passed": audit.validator_passed,
                    "validator_reason": audit.validator_reason,
                    "execution_status": (audit.execution_result or {}).get("status"),
                    "sizing": audit.decision_payload.get("sizing"),
                    "forecast_symbols": sorted(forecasts.keys()),
                }
            )
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                break
            sleep_for = min(float(args.cycle_interval_sec), remaining)
            if sleep_for <= 0.0:
                break
            await asyncio.sleep(sleep_for)
            if time.monotonic() >= deadline and len(cycles) >= 1:
                # Allow a 2nd cycle if loop boundary landed cleanly on duration
                if len(cycles) == 1 and float(args.duration_minutes) > 0:
                    continue
                break
    finally:
        watcher.stop()
        try:
            await asyncio.wait_for(watcher_task, timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            watcher_task.cancel()
            with contextlib.suppress(Exception):
                await watcher_task

        if not dry_run and bool(args.close_on_exit):
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

        usage_summary = usage_ledger.summarize()
        await transport.close()
        thesis_store.close()
        usage_ledger.close()

    prefix_hashes = [c["prefix_hash"] for c in cycles]
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
        "symbols": list(symbols),
        "cycles_count": len(cycles),
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
    parser.add_argument("--reuse-agy-session", action="store_true", default=False, help="Enable experimental --resume chaining in AgyDirectCliTransport")
    parser.add_argument("--max-margin-vst", type=float, default=1000.0)
    parser.add_argument("--max-leverage", type=int, default=50)
    parser.add_argument("--max-open-positions", type=int, default=3)
    parser.add_argument("--max-notional-vst", type=float, default=50_000.0)
    parser.add_argument("--stage1-timeout", type=float, default=90.0)
    parser.add_argument("--stage2-timeout", type=float, default=150.0)
    exit_grp = parser.add_mutually_exclusive_group()
    exit_grp.add_argument("--close-on-exit", dest="close_on_exit", action="store_true", default=True, help="Close scoped positions & cancel orders on exit (default)")
    exit_grp.add_argument("--keep-positions-on-exit", dest="close_on_exit", action="store_false", help="Leave scoped positions open on exit")
    parser.add_argument("--ledger-db", type=Path, default=DEFAULT_USAGE_LEDGER_DB)
    parser.add_argument("--order-ledger-db", type=Path, default=DEFAULT_LEDGER_DB)
    parser.add_argument("--thesis-db", type=Path, default=None)
    parser.add_argument("--journal-path", type=Path, default=DEFAULT_JOURNAL_PATH)
    parser.add_argument("--session-summary-ram", type=Path, default=None, help="Output path in /dev/shm/quantumedge/sessions")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = asyncio.run(run_persistent_session(args))
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
