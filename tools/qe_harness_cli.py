#!/usr/bin/env python3
"""QuantumEdge Harness CLI Bridge for BingX VST Sandbox & Dry-Run Execution.

Subcommands:
  status
      Read-only VST account balance, open positions, and open orders.
  context --symbol BTCUSDT [--out runtime/latest_context.json]
      Fetch full uncompressed FullMarketContext from BingX VST sandbox.
  execute --decision-json '<JSON>' [--dry-run | --live-vst]
      Validate and execute a TradeDecision via HarnessBridge (default: --dry-run).
  run-cycle [--dry-run | --live-vst] [--transport agy-proxy|gemini-direct|mock]
      Run a 2-stage Progressive Disclosure decision cycle.
      IMPORTANT (UsageLedger isolation): When using `--transport agy-proxy`,
      `AgyProxyTransport` itself writes full `UsageRecordV1` telemetry (including
      `decision_id`, `stage`, `prefix_hash`, `context_hash`, `snapshot_id`) to
      `--ledger-db`. Launch `tools/agy_openai_proxy.py` WITHOUT sharing the same
      `--ledger-db` (JSONL log only) to prevent double-counting records.
"""

from __future__ import annotations

from datetime import datetime, timezone
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Sequence
import uuid

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from quantum_edge_core.contracts.lean import TradeDecision
from quantum_edge_core.execution.bingx_vst_adapter import (
    cancel_vst_open_orders,
    create_bingx_vst_client,
    fetch_full_vst_context,
    fetch_vst_status,
    to_ccxt_swap_symbol,
)
from quantum_edge_core.execution.harness_bridge import (
    HarnessBridge,
    PaperExchangeClient,
)
from quantum_edge_core.execution.lean_executor import LeanExecutor
from quantum_edge_core.execution.order_ledger import OrderLedger
from quantum_edge_core.execution.risk_guard import (
    QUANTUMEDGE_DEFAULT_UNIVERSE,
    LeanRiskGuard,
    RiskConfig,
)


DEFAULT_LEDGER_DB = ROOT / "runtime" / "lean_orders.sqlite3"
DEFAULT_USAGE_LEDGER_DB = ROOT / "runtime" / "usage_ledger.sqlite3"
DEFAULT_JOURNAL_PATH = ROOT / "runtime" / "lean_trade_journal.jsonl"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qe_harness_cli",
        description="QuantumEdge Harness CLI for BingX VST Sandbox and Paper Execution",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # status
    sub.add_parser(
        "status",
        help="Read-only BingX VST balance, open positions, and open orders",
    )

    # context
    p_ctx = sub.add_parser(
        "context",
        help="Fetch uncompressed FullMarketContext from BingX VST sandbox",
    )
    p_ctx.add_argument("--symbol", required=True, help="Trading symbol, e.g. BTCUSDT")
    p_ctx.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Optional output file path for FullMarketContext JSON",
    )
    p_ctx.add_argument(
        "--timeframes",
        default="1m,5m,15m,1h,4h",
        help="Comma-separated OHLCV timeframes (default: 1m,5m,15m,1h,4h)",
    )
    p_ctx.add_argument(
        "--ohlcv-limit",
        type=int,
        default=100,
        help="Candles per timeframe (default: 100)",
    )
    p_ctx.add_argument(
        "--orderbook-limit",
        type=int,
        default=50,
        help="Orderbook depth per side (default: 50)",
    )
    p_ctx.add_argument(
        "--allowed-symbols",
        default=os.getenv("QE_ALLOWED_SYMBOLS", ""),
        help="Comma-separated allowed symbols override (default: RiskConfig defaults)",
    )
    p_ctx.add_argument(
        "--no-analytics",
        action="store_true",
        default=False,
        help="Disable liqmap/orderflow/composite_signal enrichment for fast diagnostics",
    )

    # cancel-open-orders
    p_cancel = sub.add_parser(
        "cancel-open-orders",
        help="Safely cancel open orders on BingX VST sandbox (--dry-run by default)",
    )
    p_cancel.add_argument(
        "--symbol",
        required=True,
        help="Trading symbol (e.g. ETHUSDT) or ALL",
    )
    cancel_mode = p_cancel.add_mutually_exclusive_group()
    cancel_mode.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Simulate cancellation without exchange mutations (default)",
    )
    cancel_mode.add_argument(
        "--live-vst",
        action="store_true",
        default=False,
        help="Cancel open orders on BingX VST sandbox",
    )
    p_cancel.add_argument(
        "--journal-path",
        type=Path,
        default=DEFAULT_JOURNAL_PATH,
        help="Path to JSONL trade decision journal",
    )

    # execute
    p_exec = sub.add_parser(
        "execute",
        help="Execute a TradeDecision via HarnessBridge (--dry-run by default)",
    )
    p_exec.add_argument(
        "--decision-json",
        required=True,
        help="JSON string representing TradeDecision",
    )
    mode_group = p_exec.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Execute offline against PaperExchangeClient (default)",
    )
    mode_group.add_argument(
        "--live-vst",
        action="store_true",
        default=False,
        help="Execute against BingX VST sandbox (set_sandbox_mode(True))",
    )
    p_exec.add_argument(
        "--current-price",
        type=float,
        default=None,
        help="Reference market price for --dry-run (auto-fetched in --live-vst)",
    )
    p_exec.add_argument(
        "--open-positions-count",
        type=int,
        default=0,
        help="Simulated open positions count for --dry-run",
    )
    p_exec.add_argument(
        "--ledger-db",
        type=Path,
        default=DEFAULT_LEDGER_DB,
        help="Path to SQLite OrderLedger database",
    )
    p_exec.add_argument(
        "--journal-path",
        type=Path,
        default=DEFAULT_JOURNAL_PATH,
        help="Path to JSONL trade decision journal",
    )
    p_exec.add_argument(
        "--allowed-symbols",
        default=os.getenv("QE_ALLOWED_SYMBOLS", ""),
        help="Comma-separated allowed symbols override (default: RiskConfig defaults)",
    )

    # run-cycle
    p_cycle = sub.add_parser(
        "run-cycle",
        help="Run a 2-stage Progressive Disclosure decision cycle (--dry-run by default)",
    )
    cycle_mode = p_cycle.add_mutually_exclusive_group()
    cycle_mode.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Run cycle against PaperExchangeClient with zero exchange mutations (default)",
    )
    cycle_mode.add_argument(
        "--live-vst",
        action="store_true",
        default=False,
        help="Run cycle against BingX VST sandbox via HarnessBridge",
    )
    p_cycle.add_argument(
        "--transport",
        choices=("mock", "agy-proxy", "gemini-direct"),
        default="agy-proxy",
        help="LLM transport backend (default: agy-proxy OAuth proxy)",
    )
    p_cycle.add_argument(
        "--proxy-url",
        default="http://127.0.0.1:8045/v1",
        help="Local agy_openai_proxy URL (default: http://127.0.0.1:8045/v1)",
    )
    p_cycle.add_argument(
        "--model",
        default="gemini-4-carbon",
        help="Canonical Gemini 4 model ID (default: gemini-4-carbon)",
    )
    p_cycle.add_argument(
        "--symbols",
        default=",".join(QUANTUMEDGE_DEFAULT_UNIVERSE),
        help="Comma-separated symbols (default: BNBUSDT,LINKUSDT,SUIUSDT)",
    )
    p_cycle.add_argument(
        "--max-margin-vst",
        type=float,
        default=1000.0,
        help="Max isolated margin per pair in VST (default: 1000.0)",
    )
    p_cycle.add_argument(
        "--max-leverage",
        type=int,
        default=50,
        help="Max leverage multiplier per pair (default: 50)",
    )
    p_cycle.add_argument(
        "--max-open-positions",
        type=int,
        default=3,
        help="Max simultaneous open positions across scoped universe (default: 3)",
    )
    p_cycle.add_argument(
        "--max-notional-vst",
        type=float,
        default=50_000.0,
        help="Max notional per pair in VST (default: 50000.0)",
    )
    p_cycle.add_argument(
        "--trigger",
        default="HEARTBEAT_1H",
        help="Cycle trigger summary (default: HEARTBEAT_1H)",
    )
    img_group = p_cycle.add_mutually_exclusive_group()
    img_group.add_argument(
        "--include-images",
        dest="include_images",
        action="store_true",
        default=None,
        help="Force WebP image attachments in Stage 1/2 prompts",
    )
    img_group.add_argument(
        "--no-images",
        dest="include_images",
        action="store_false",
        help="Disable WebP image attachments (default for agy-proxy and mock)",
    )
    p_cycle.add_argument(
        "--ledger-db",
        type=Path,
        default=DEFAULT_USAGE_LEDGER_DB,
        help=(
            "Path to SQLite UsageLedger DB (AgyProxyTransport writes full UsageRecordV1 here; "
            "do NOT share this --ledger-db with agy_openai_proxy.py to avoid double-counting)"
        ),
    )
    p_cycle.add_argument(
        "--stage1-timeout",
        type=float,
        default=90.0,
        help="Stage 1 screening LLM request timeout in seconds (default: 90.0)",
    )
    p_cycle.add_argument(
        "--stage2-timeout",
        type=float,
        default=150.0,
        help="Stage 2 drill-down LLM request timeout in seconds (default: 150.0)",
    )
    p_cycle.add_argument(
        "--order-ledger-db",
        type=Path,
        default=DEFAULT_LEDGER_DB,
        help="Path to SQLite OrderLedger database",
    )
    p_cycle.add_argument(
        "--journal-path",
        type=Path,
        default=DEFAULT_JOURNAL_PATH,
        help="Path to JSONL trade decision journal",
    )
    p_cycle.add_argument(
        "--fixture",
        type=Path,
        default=None,
        help="Optional context JSON fixture for offline --dry-run",
    )
    p_cycle.add_argument(
        "--dump-ram",
        action="store_true",
        default=False,
        help="Dump cycle DecisionAuditV1 JSON to /dev/shm/quantumedge/cycles",
    )
    p_cycle.add_argument(
        "--thesis-db",
        type=Path,
        default=None,
        help="Path to SQLite ThesisMemoryStore (default: runtime/thesis_memory.sqlite3 or RAM in mock dry-run)",
    )
    p_cycle.add_argument(
        "--stateless",
        action="store_true",
        default=False,
        help="Use StatelessStagePromptAssembler instead of 3-layer PrefixContextAssembler",
    )
    p_cycle.add_argument(
        "--fixed-thinking-medium",
        action="store_true",
        default=False,
        help="Disable adaptive ReasoningEffortRouter and use fixed MEDIUM on Stage 2",
    )

    # protect-once
    p_prot = sub.add_parser(
        "protect-once",
        help="Run a single deterministic PositionProtectionWatcher check (--dry-run by default)",
    )
    prot_mode = p_prot.add_mutually_exclusive_group()
    prot_mode.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Inspect protection triggers without exchange mutations (default)",
    )
    prot_mode.add_argument(
        "--live-vst",
        action="store_true",
        default=False,
        help="Execute orphan cleanup / emergency exits on BingX VST sandbox",
    )
    p_prot.add_argument(
        "--stale-limit-sec",
        type=float,
        default=30.0,
        help="Stale feed threshold in seconds (default: 30.0)",
    )
    p_prot.add_argument(
        "--max-liq-pct",
        type=float,
        default=2.0,
        help="Liquidation distance emergency threshold in percent (default: 2.0)",
    )
    p_prot.add_argument(
        "--order-ledger-db",
        type=Path,
        default=DEFAULT_LEDGER_DB,
        help="Path to SQLite OrderLedger database",
    )
    p_prot.add_argument(
        "--journal-path",
        type=Path,
        default=DEFAULT_JOURNAL_PATH,
        help="Path to JSONL trade decision journal",
    )
    p_prot.add_argument(
        "--mock-status-json",
        default=None,
        help="Optional inline JSON status dict for offline dry-run verification",
    )
    p_prot.add_argument(
        "--allowed-symbols",
        default=None,
        help="Comma-separated allowed symbols for PositionProtectionWatcher",
    )
    p_prot.add_argument(
        "--read-live-vst",
        action="store_true",
        default=False,
        help="In --dry-run mode, read real BingX VST status instead of offline mock/paper client",
    )

    return parser


def _build_risk_config(allowed_symbols_raw: str | None) -> RiskConfig:
    if allowed_symbols_raw and allowed_symbols_raw.strip():
        syms = tuple(
            s.strip().upper()
            for s in allowed_symbols_raw.split(",")
            if s.strip()
        )
        if syms:
            return RiskConfig(allowed_symbols=syms)
    return RiskConfig()


def _cmd_status() -> int:
    client = create_bingx_vst_client()
    status_payload = fetch_vst_status(client)
    print(json.dumps(status_payload, indent=2, ensure_ascii=False))
    return 0


def _cmd_context(args: argparse.Namespace) -> int:
    client = create_bingx_vst_client()
    tfs = tuple(tf.strip() for tf in args.timeframes.split(",") if tf.strip())
    include_analytics = not bool(args.no_analytics)
    ctx = fetch_full_vst_context(
        client,
        symbol=args.symbol,
        risk_config=_build_risk_config(args.allowed_symbols),
        timeframes=tfs,
        ohlcv_limit=args.ohlcv_limit,
        orderbook_limit=args.orderbook_limit,
        include_analytics=include_analytics,
    )
    payload_json = ctx.model_dump_json(indent=2)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload_json, encoding="utf-8")
        micro = ctx.microstructure_and_liquidity
        analytics_status = (
            {
                k: micro.get(k, {}).get("status")
                for k in ("liqmap", "orderflow", "composite_signal")
            }
            if include_analytics
            else None
        )
        summary = {
            "status": "ok",
            "sandbox": True,
            "symbol": ctx.market_snapshot.symbol,
            "out": str(args.out),
            "bytes": len(payload_json.encode("utf-8")),
            "timeframes": list(ctx.ohlcv_by_tf.keys()),
            "analytics_enabled": include_analytics,
            "analytics_status": analytics_status,
            "provider_gets": ctx.raw_extras.get("provider_gets"),
        }
        print(json.dumps(summary, ensure_ascii=False))
    else:
        print(payload_json)
    return 0


def _cmd_cancel_open_orders(args: argparse.Namespace) -> int:
    sym = args.symbol.strip().upper()
    if args.live_vst:
        client = create_bingx_vst_client()
        res = cancel_vst_open_orders(client, symbol=sym)
        payload = {
            "status": "ok" if not res["errors"] else "partial",
            "mode": "live_vst",
            **res,
        }
    else:
        payload = {
            "status": "ok",
            "mode": "dry_run",
            "sandbox": True,
            "symbol": sym,
            "found_open_orders": 0,
            "cancelled_count": 0,
            "cancelled_order_ids": [],
            "errors": [],
        }

    args.journal_path.parent.mkdir(parents=True, exist_ok=True)
    journal_row = {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "decision_id": f"cancel-open-orders-{sym.lower()}-{int(datetime.now(timezone.utc).timestamp())}",
        "symbol": sym,
        "action": "CANCEL_OPEN_ORDERS",
        "mode": payload["mode"],
        "cancelled_count": payload["cancelled_count"],
        "cancelled_order_ids": payload["cancelled_order_ids"],
        "order_status": "CANCELLED" if payload["cancelled_count"] > 0 else "NOOP",
        "error": None if not payload["errors"] else json.dumps(payload["errors"], ensure_ascii=False),
    }
    with args.journal_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(journal_row, ensure_ascii=False) + "\n")

    print(json.dumps(payload, ensure_ascii=False))
    return 0


def _cmd_execute(args: argparse.Namespace) -> int:
    raw_decision = json.loads(args.decision_json)
    decision = TradeDecision.model_validate(raw_decision)

    ledger = OrderLedger(args.ledger_db)
    risk_guard = LeanRiskGuard(_build_risk_config(args.allowed_symbols))

    if args.live_vst:
        client = create_bingx_vst_client()
        ccxt_sym = to_ccxt_swap_symbol(decision.symbol)
        ticker = client.fetch_ticker(ccxt_sym)
        current_price = float(
            ticker.get("last") or ticker.get("bid") or ticker.get("ask") or 0.0
        )
        vst_status = fetch_vst_status(client)
        open_positions_count = int(vst_status["open_positions_count"])
        position_qty = None
        if decision.action in ("CLOSE_LONG", "CLOSE_SHORT") and decision.qty is None:
            target_side = "LONG" if decision.action == "CLOSE_LONG" else "SHORT"
            for pos in vst_status["open_positions"]:
                if (
                    pos.get("symbol") == decision.symbol.upper()
                    and str(pos.get("positionSide") or "").upper() == target_side
                ):
                    position_qty = float(pos["contracts"])
                    break
        executor = LeanExecutor(client, ledger, risk_guard)
        bridge = HarnessBridge(executor, journal_path=args.journal_path)
        result = bridge.submit_decision(
            decision,
            current_price=current_price,
            open_positions_count=open_positions_count,
            position_qty=position_qty,
        )
    else:
        # Default safe mode: --dry-run (PaperExchangeClient, 0 exchange mutations)
        paper_client = PaperExchangeClient()
        current_price = (
            float(args.current_price)
            if args.current_price is not None
            else float(decision.entry_price or 100.0)
        )
        executor = LeanExecutor(paper_client, ledger, risk_guard)
        bridge = HarnessBridge(executor, journal_path=args.journal_path)
        result = bridge.submit_decision(
            decision,
            current_price=current_price,
            open_positions_count=args.open_positions_count,
        )

    print(result.model_dump_json())
    return 0


def _build_offline_mock_bundle(
    symbols: tuple[str, ...],
    fixture_path: Path | None = None,
) -> Any:
    from quantum_edge_core.orchestrator.deterministic_orchestrator import PreFetchedBundle

    base_ctx: dict[str, Any] | None = None
    candidate_fixture = fixture_path or (ROOT / "runtime" / "step1_enriched_ctx_btcusdt.json")
    if candidate_fixture.is_file():
        base_ctx = json.loads(candidate_fixture.read_text(encoding="utf-8"))

    contexts: dict[str, dict[str, Any]] = {}
    default_prices = {
        "BTCUSDT": (60150.0, 0.25),
        "ETHUSDT": (2420.0, 25.0),
        "SOLUSDT": (143.0, 26.0),
        "XRPUSDT": (0.58, 27.0),
        "BNBUSDT": (620.0, 1.5),
        "LINKUSDT": (15.5, 18.0),
        "SUIUSDT": (3.2, 16.0),
    }
    t0 = 1_795_641_840_000
    for sym in symbols:
        if sym == "BTCUSDT" and base_ctx is not None:
            contexts[sym] = base_ctx
            continue
        px, spr = default_prices.get(sym, (100.0, 20.0))
        bars_5m = [
            {
                "ts": t0 + i * 300_000,
                "open": px,
                "high": round(px * 1.002, 4),
                "low": round(px * 0.998, 4),
                "close": round(px * 1.0005, 4),
                "volume": 120.0,
            }
            for i in range(30)
        ]
        contexts[sym] = {
            "market_snapshot": {
                "symbol": sym,
                "bid": px,
                "ask": round(px * (1.0 + spr / 10_000.0), 6),
                "last_price": px,
                "spread_bps": spr,
                "is_stale": False,
            },
            "ohlcv_by_tf": {"4h": bars_5m, "1h": bars_5m, "15m": bars_5m, "5m": bars_5m, "1m": bars_5m},
            "orderbook": {"bids": [[px, 10.0]], "asks": [[round(px * 1.001, 6), 10.0]]},
            "derivatives": {"funding_rate": 0.0001, "open_interest": 50000.0},
            "microstructure_and_liquidity": {
                "liqmap": {"status": "OK", "symbol": sym, "timeframe": "1h", "regime": "flat", "last_price": px, "zones": []},
                "orderflow": {"status": "OK", "imbalance": 0.12, "cvd_or_delta": 15.0, "metrics": {"imbalance_top10": 0.12}},
                "composite_signal": {"status": "OK", "signal": "LONG", "score": 35, "components": {}},
            },
        }

    return PreFetchedBundle(
        snapshot_id=f"snap-mock-{uuid.uuid4().hex[:8]}",
        vst_status={
            "sandbox": True,
            "balance_vst": {"free": 9500.0, "total": 10000.0, "used": 500.0},
            "open_positions_count": 0,
            "open_positions": [],
            "open_orders_count": 0,
            "open_orders": [],
        },
        contexts_by_symbol=contexts,
        fetch_ms=0.5,
    )


def _cmd_run_cycle(args: argparse.Namespace) -> int:
    from quantum_edge_core.contracts.optimization import (
        RequestStatus,
        TransportChannel,
        UsageRecordV1,
    )
    from quantum_edge_core.execution.storage_policy import (
        CANONICAL_SSD_DURABLE_PATHS,
        get_ram_tmp_dir,
    )
    from quantum_edge_core.orchestrator.deterministic_orchestrator import (
        DeterministicTradingOrchestrator,
        PreFetchCoordinator,
        StatelessStagePromptAssembler,
    )
    from quantum_edge_core.orchestrator.prefix_context_assembler import (
        PrefixContextAssembler,
        ThesisMemoryStore,
    )
    from quantum_edge_ml.model_router.backends.google_gemini import GeminiDirectClient
    from quantum_edge_ml.model_router.backends.transport import (
        AgyProxyTransport,
        ImageAttachment,
        LLMRequest,
        LLMResponse,
        LLMTransport,
    )
    from quantum_edge_ml.model_router.reasoning_router import ReasoningEffortRouter
    from quantum_edge_ml.model_router.usage_ledger import UsageLedger

    class _CliMockTransport(LLMTransport):
        def __init__(self, ledger: UsageLedger, default_symbol: str = "BNBUSDT") -> None:
            super().__init__(ledger)
            self.default_symbol = default_symbol

        async def generate(self, req: LLMRequest) -> LLMResponse:
            rec_id = f"mock-{uuid.uuid4().hex[:12]}"
            in_chars = len(req.system_instruction) + len(req.combined_user_text)
            if req.stage == "stage1_screen":
                txt = json.dumps(
                    {
                        "market_regime_summary": f"{self.default_symbol} 1h constructive; alt spreads wide.",
                        "action": "HOLD",
                        "candidate_symbol": self.default_symbol,
                        "hold_blocker_code": "NO_LIQ_ASYMMETRY",
                        "candidate_rationale": "Offline mock fast screen HOLD.",
                        "estimated_edge_bps": 12.0,
                    }
                )
            else:
                txt = "{}"
            usage = UsageRecordV1(
                record_id=rec_id,
                decision_id=req.decision_id,
                stage=req.stage,
                transport=TransportChannel.AGY_CLI_PROXY,
                model_provider_id=req.model,
                thinking_level=req.thinking_level,
                request_status=RequestStatus.OK,
                provider_input_tokens=max(1, in_chars // 4),
                cache_read_tokens=0,
                output_tokens=45,
                thinking_tokens=120,
                total_tokens=max(1, in_chars // 4) + 165,
                input_chars=in_chars,
                output_chars=len(txt),
                latency_ms=5.0,
                timeout_ms=req.timeout_sec * 1000.0,
            )
            self.ledger.record_usage(usage)
            return LLMResponse(text=txt, usage=usage)

        async def count_tokens(
            self, model: str, text_parts: list[str], images: list[ImageAttachment] | None = None
        ) -> dict[str, int]:
            return {"text_tokens": 0, "image_tokens_media_res": 0, "total_tokens": 0}

        async def close(self) -> None:
            return None

    symbols = tuple(s.strip().upper() for s in args.symbols.split(",") if s.strip())
    dry_run = not bool(args.live_vst)
    include_images = (
        args.include_images
        if args.include_images is not None
        else (args.transport == "gemini-direct")
    )

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

    if args.transport == "mock":
        transport: LLMTransport = _CliMockTransport(
            usage_ledger, default_symbol=symbols[0] if symbols else "BNBUSDT"
        )
    elif args.transport == "gemini-direct":
        transport = GeminiDirectClient(usage_ledger, mode="interactions")
    else:
        # AgyProxyTransport writes full UsageRecordV1 (decision_id/stage/prefix_hash/context_hash)
        # into usage_ledger; agy_openai_proxy.py should run without --ledger-db (JSONL log only).
        transport = AgyProxyTransport(usage_ledger, base_url=args.proxy_url, record_to_ledger=True)

    async def _execute_cycle() -> dict[str, Any]:
        try:
            if args.live_vst:
                ex_client: Any = create_bingx_vst_client()
                prefetch = PreFetchCoordinator(ex_client, symbols=symbols, risk_config=risk_cfg)
                bundle = await prefetch.fetch_all(include_analytics=True)
            else:
                ex_client = PaperExchangeClient()
                if args.transport == "mock" or args.fixture is not None:
                    bundle = _build_offline_mock_bundle(symbols, fixture_path=args.fixture)
                else:
                    vst_client = create_bingx_vst_client()
                    prefetch = PreFetchCoordinator(vst_client, symbols=symbols, risk_config=risk_cfg)
                    bundle = await prefetch.fetch_all(include_analytics=True)

            executor = LeanExecutor(ex_client, order_ledger, risk_guard)
            bridge = HarnessBridge(executor, journal_path=args.journal_path)
            thesis_store: ThesisMemoryStore | None = None
            if args.stateless:
                assembler: Any = StatelessStagePromptAssembler(include_images=include_images)
            else:
                thesis_db_path = args.thesis_db
                if thesis_db_path is None:
                    if args.transport == "mock" and dry_run:
                        thesis_db_path = get_ram_tmp_dir("cycles") / "mock_thesis_memory.sqlite3"
                    else:
                        thesis_db_path = CANONICAL_SSD_DURABLE_PATHS["thesis_memory"]
                thesis_store = ThesisMemoryStore(thesis_db_path)
                assembler = PrefixContextAssembler(
                    thesis_store=thesis_store,
                    include_images=include_images,
                )
            effort_router = (
                None
                if args.fixed_thinking_medium
                else ReasoningEffortRouter(model_id=args.model)
            )
            orchestrator = DeterministicTradingOrchestrator(
                transport=transport,
                ledger=usage_ledger,
                assembler=assembler,
                bridge=bridge,
                effort_router=effort_router,
                model_id=args.model,
                stage1_timeout_sec=args.stage1_timeout,
                stage2_timeout_sec=args.stage2_timeout,
            )
            try:
                audit = await orchestrator.run_decision_cycle(
                    bundle,
                    trigger_summary=args.trigger,
                    dry_run=dry_run,
                )
                out_dict = audit.model_dump(mode="json")
                out_dict["stage2_thinking_level"] = audit.thinking_level.value
                out_dict["effort_router_enabled"] = effort_router is not None
                if isinstance(assembler, PrefixContextAssembler):
                    cp = assembler.snapshot_store.current
                    out_dict["resync_mode"] = "RESYNC" if cp is not None else "UNKNOWN"
                    out_dict["cache_eviction_alert"] = (
                        assembler.snapshot_store.eviction_monitor.alert_active
                    )
                if args.dump_ram:
                    ram_file = get_ram_tmp_dir("cycles") / f"{audit.decision_id}.json"
                    ram_file.write_text(json.dumps(out_dict, indent=2), encoding="utf-8")
                    out_dict["ram_dump_path"] = str(ram_file)
                return out_dict
            finally:
                if thesis_store is not None:
                    thesis_store.close()
        finally:
            await transport.close()
            usage_ledger.close()

    summary = asyncio.run(_execute_cycle())
    print(json.dumps(summary, ensure_ascii=False))
    return 0


def _cmd_protect_once(args: argparse.Namespace) -> int:
    from quantum_edge_core.execution.position_protection import PositionProtectionWatcher

    dry_run = not bool(args.live_vst)
    order_ledger = OrderLedger(args.order_ledger_db)
    risk_cfg = RiskConfig()
    risk_guard = LeanRiskGuard(risk_cfg)

    raw_allowed = args.allowed_symbols or os.environ.get("QE_ALLOWED_SYMBOLS")
    if raw_allowed:
        allowed_symbols = tuple(
            s.strip().upper() for s in str(raw_allowed).split(",") if s.strip()
        )
    elif args.live_vst or args.read_live_vst:
        allowed_symbols = QUANTUMEDGE_DEFAULT_UNIVERSE
    else:
        allowed_symbols = tuple(risk_cfg.allowed_symbols)

    if args.live_vst:
        ex_client: Any = create_bingx_vst_client()
        source = "bingx_vst_live"
    elif args.read_live_vst:
        ex_client = create_bingx_vst_client()
        source = "bingx_vst_readonly"
    else:
        source = "mock_status" if args.mock_status_json else "paper_client"
        mock_state = (
            json.loads(args.mock_status_json)
            if args.mock_status_json
            else {"balance": {"VST": {"free": 10000.0, "total": 10000.0, "used": 0.0}}, "positions": [], "orders": []}
        )

        class _OfflineStatusClient(PaperExchangeClient):
            _qe_sandbox_verified = True

            def fetch_balance(self) -> dict[str, Any]:
                return mock_state.get("balance") or {"VST": {"free": 10000.0, "total": 10000.0, "used": 0.0}}

            def fetch_positions(self) -> list[dict[str, Any]]:
                return list(mock_state.get("positions") or [])

            def fetch_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
                return list(mock_state.get("orders") or [])

            def cancel_order(self, oid: str, sym: str) -> dict[str, Any]:
                return {"id": oid, "status": "canceled"}

        ex_client = _OfflineStatusClient()

    executor = LeanExecutor(ex_client, order_ledger, risk_guard)
    bridge = HarnessBridge(executor, journal_path=args.journal_path)
    watcher = PositionProtectionWatcher(
        ex_client,
        bridge,
        allowed_symbols=allowed_symbols,
        max_liq_distance_pct=args.max_liq_pct,
        stale_feed_limit_sec=args.stale_limit_sec,
        dry_run=dry_run,
    )
    res = asyncio.run(watcher.check_once())
    res["source"] = source
    print(json.dumps(res, ensure_ascii=False))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "status":
        return _cmd_status()
    if args.command == "context":
        return _cmd_context(args)
    if args.command == "cancel-open-orders":
        return _cmd_cancel_open_orders(args)
    if args.command == "execute":
        return _cmd_execute(args)
    if args.command == "run-cycle":
        return _cmd_run_cycle(args)
    if args.command == "protect-once":
        return _cmd_protect_once(args)
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
