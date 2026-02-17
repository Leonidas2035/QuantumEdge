"""
Backtest Metrics Calculator.
"""

import numpy as np
from typing import List, Dict, Any
from quantum_edge_core.backtesting.mock_exchange import Trade


class BacktestMetrics:
    @staticmethod
    def calculate_stats(
        trades: List[Trade], equity_curve: List[float], initial_balance: float
    ) -> Dict[str, Any]:
        """
        Calculate performance metrics from trade list and equity curve.
        """
        if not trades and not equity_curve:
            return {
                "total_trades": 0,
                "win_rate": 0.0,
                "total_pnl_pct": 0.0,
                "max_drawdown_pct": 0.0,
                "sharpe_ratio": 0.0,
                "profit_factor": 0.0,
            }

        # PnL per trade is harder to track with MockExchange's simple trade log (it logs fills, not round-trips).
        # We need to rely on equity curve for global stats.

        # 1. Total PnL
        final_equity = equity_curve[-1]
        total_pnl = final_equity - initial_balance
        total_pnl_pct = (total_pnl / initial_balance) * 100

        # 2. Max Drawdown
        equity_arr = np.array(equity_curve)
        if len(equity_arr) == 0:
            max_drawdown_pct = 0.0
        else:
            rolling_max = np.maximum.accumulate(equity_arr)
            drawdown = (equity_arr - rolling_max) / rolling_max
            max_drawdown_pct = np.min(drawdown) * 100 if len(drawdown) > 0 else 0.0

        # 3. Sharpe Ratio
        if len(equity_arr) > 1:
            returns = np.diff(equity_arr) / equity_arr[:-1]
            if np.std(returns) > 0:
                sharpe = (np.mean(returns) / np.std(returns)) * np.sqrt(len(returns))
            else:
                sharpe = 0.0
        else:
            sharpe = 0.0

        return {
            "total_trades": len(trades),
            # Win Rate requires reconstructing round-trip trades.
            # For HFT, we often just look at daily PnL or total equity.
            # We'll skip per-trade win rate for now unless we implement trade pairing.
            "win_rate": 0.0,
            "total_pnl_pct": round(total_pnl_pct, 2),
            "max_drawdown_pct": round(max_drawdown_pct, 2),
            "sharpe_ratio": round(sharpe, 2),
            "profit_factor": 0.0,  # Requires gross profit/loss
            "final_equity": round(final_equity, 2),
        }
