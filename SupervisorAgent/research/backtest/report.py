"""Report writers for backtest results."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .engine import BacktestResult, TradeFill, EquityPoint


def _write_trades(trades: Iterable[TradeFill], path: Path) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for trade in trades:
            handle.write(json.dumps(trade.__dict__, sort_keys=True))
            handle.write("\n")


def _write_equity_curve(curve: Iterable[EquityPoint], path: Path) -> None:
    with path.open("w", encoding="utf-8") as handle:
        handle.write("ts,equity\n")
        for point in curve:
            handle.write(f"{point.ts},{point.equity:.8f}\n")


def _summary_markdown(result: BacktestResult) -> str:
    metrics = result.metrics
    lines = [
        "# Backtest Summary",
        "",
        f"Symbol: `{result.symbol}`",
        f"Started: `{int(result.started_at)}`",
        f"Finished: `{int(result.finished_at)}`",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Total PnL | {metrics.get('total_pnl', 0.0):.4f} |",
        f"| Realized PnL | {metrics.get('realized_pnl', 0.0):.4f} |",
        f"| Max Drawdown | {metrics.get('max_drawdown', 0.0):.4f} |",
        f"| Trades | {metrics.get('trades', 0)} |",
        f"| Win Rate | {metrics.get('win_rate', 0.0):.2%} |",
        f"| Avg Win | {metrics.get('avg_win', 0.0):.4f} |",
        f"| Avg Loss | {metrics.get('avg_loss', 0.0):.4f} |",
        f"| Sharpe (simple) | {metrics.get('sharpe', 0.0):.4f} |",
        "",
    ]
    return "\n".join(lines)


def write_backtest_reports(
    result: BacktestResult,
    out_dir: Path,
    write_equity_curve: bool = True,
    write_trades: bool = True,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / "results.json"
    results_path.write_text(json.dumps(result.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    summary_path = out_dir / "summary.md"
    summary_path.write_text(_summary_markdown(result), encoding="utf-8")
    if write_trades:
        _write_trades(result.trades, out_dir / "trades.jsonl")
    if write_equity_curve:
        _write_equity_curve(result.equity_curve, out_dir / "equity_curve.csv")
    return out_dir
