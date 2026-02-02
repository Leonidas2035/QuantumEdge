"""Metrics for research backtests."""

from __future__ import annotations

import math
from typing import List, Protocol


class TradeFillLike(Protocol):
    action: str
    pnl: float


class EquityPointLike(Protocol):
    equity: float


def _drawdown(equity_curve: List[EquityPointLike]) -> float:
    peak = None
    max_dd = 0.0
    for point in equity_curve:
        if peak is None or point.equity > peak:
            peak = point.equity
        if peak is not None:
            dd = peak - point.equity
            if dd > max_dd:
                max_dd = dd
    return max_dd


def _sharpe(equity_curve: List[EquityPointLike]) -> float:
    if len(equity_curve) < 3:
        return 0.0
    returns = []
    for prev, curr in zip(equity_curve, equity_curve[1:]):
        if prev.equity == 0:
            continue
        returns.append((curr.equity - prev.equity) / abs(prev.equity))
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    std = math.sqrt(var)
    if std == 0:
        return 0.0
    return mean / std * math.sqrt(len(returns))


def compute_metrics(trades: List[TradeFillLike], equity_curve: List[EquityPointLike]) -> dict:
    realized = sum(t.pnl for t in trades if t.action.startswith("close"))
    total = equity_curve[-1].equity if equity_curve else realized
    closes = [t for t in trades if t.action.startswith("close")]
    wins = [t for t in closes if t.pnl > 0]
    losses = [t for t in closes if t.pnl < 0]
    win_rate = (len(wins) / len(closes)) if closes else 0.0
    avg_win = sum(t.pnl for t in wins) / len(wins) if wins else 0.0
    avg_loss = sum(t.pnl for t in losses) / len(losses) if losses else 0.0
    return {
        "total_pnl": total,
        "realized_pnl": realized,
        "max_drawdown": _drawdown(equity_curve),
        "trades": len(closes),
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "sharpe": _sharpe(equity_curve),
    }
