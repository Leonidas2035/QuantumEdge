#!/usr/bin/env python3
"""
CLI Script to Run Backtests.
Usage: python scripts/run_backtest.py --symbol BTCUSDT --days 7
"""

import argparse
import logging
from datetime import datetime, timedelta
import sys
import os

# Add src to pythonpath
sys.path.append(os.path.abspath("src"))
sys.path.append(os.path.abspath("src/quantum_edge_core/strategies/scalper_v1"))

from quantum_edge_core.backtesting.runner import BacktestRunner

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("BacktestCLI")


def parse_args():
    parser = argparse.ArgumentParser(description="Run Event-Driven Backtest")
    parser.add_argument("--symbol", type=str, default="BTCUSDT", help="Symbol to backtest")
    parser.add_argument("--days", type=int, default=1, help="Number of days to look back")
    parser.add_argument("--start", type=str, help="Start Date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, help="End Date (YYYY-MM-DD)")
    parser.add_argument("--db-host", type=str, default="http://localhost:9000", help="QuestDB HTTP Host")
    return parser.parse_args()


def main():
    args = parse_args()

    # Time Range
    if args.start:
        start_time = datetime.strptime(args.start, "%Y-%m-%d")
    else:
        start_time = datetime.utcnow() - timedelta(days=args.days)

    if args.end:
        end_time = datetime.strptime(args.end, "%Y-%m-%d")
    else:
        end_time = datetime.utcnow()

    logger.info(f"Backtesting {args.symbol} from {start_time} to {end_time}")

    runner = BacktestRunner(symbol=args.symbol, start_time=start_time, end_time=end_time, db_host=args.db_host)

    try:
        stats = runner.run()
        print("\n=== BACKTEST RESULTS ===")
        print(f"Total Trades: {stats['total_trades']}")
        print("Win Rate:     N/A")  # metrics.py says N/A yet
        print(f"Total PnL:    {stats['total_pnl_pct']}%")
        print(f"Final Equity: ${stats['final_equity']}")
        print(f"Max Drawdown: {stats['max_drawdown_pct']}%")
        print(f"Sharpe Ratio: {stats['sharpe_ratio']}")
        print("========================\n")

    except KeyboardInterrupt:
        logger.warning("Backtest interrupted.")
    except Exception as e:
        logger.error(f"Backtest Failed: {e}", exc_info=True)


if __name__ == "__main__":
    main()
