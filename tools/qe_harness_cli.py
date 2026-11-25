#!/usr/bin/env python3
"""QuantumEdge Harness CLI Bridge for BingX VST Sandbox & Dry-Run Execution.

Subcommands:
  status
      Read-only VST account balance, open positions, and open orders.
  context --symbol BTCUSDT [--out runtime/latest_context.json]
      Fetch full uncompressed FullMarketContext from BingX VST sandbox.
  execute --decision-json '<JSON>' [--dry-run | --live-vst]
      Validate and execute a TradeDecision via HarnessBridge (default: --dry-run).
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from quantum_edge_core.contracts.lean import TradeDecision
from quantum_edge_core.execution.bingx_vst_adapter import (
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
from quantum_edge_core.execution.risk_guard import LeanRiskGuard, RiskConfig


DEFAULT_LEDGER_DB = ROOT / "runtime" / "lean_orders.sqlite3"
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
    ctx = fetch_full_vst_context(
        client,
        symbol=args.symbol,
        risk_config=_build_risk_config(args.allowed_symbols),
        timeframes=tfs,
        ohlcv_limit=args.ohlcv_limit,
        orderbook_limit=args.orderbook_limit,
    )
    payload_json = ctx.model_dump_json(indent=2)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload_json, encoding="utf-8")
        summary = {
            "status": "ok",
            "sandbox": True,
            "symbol": ctx.market_snapshot.symbol,
            "out": str(args.out),
            "bytes": len(payload_json.encode("utf-8")),
            "timeframes": list(ctx.ohlcv_by_tf.keys()),
            "provider_gets": ctx.raw_extras.get("provider_gets"),
        }
        print(json.dumps(summary, ensure_ascii=False))
    else:
        print(payload_json)
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "status":
        return _cmd_status()
    if args.command == "context":
        return _cmd_context(args)
    if args.command == "execute":
        return _cmd_execute(args)
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
