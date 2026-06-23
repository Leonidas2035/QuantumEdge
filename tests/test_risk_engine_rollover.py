import os
import sys
import unittest
from datetime import date, timedelta
from unittest.mock import MagicMock

# Add src to PYTHONPATH
sys.path.insert(0, os.path.abspath("src"))

from hermes.supervisor.risk_engine import HardRiskEngine


class MockRiskState:
    def __init__(self):
        self.trading_day = date.today()
        self.equity_start = None
        self.equity_now = None
        self.realized_pnl_today = None
        self.max_equity_intraday = None
        self.min_equity_intraday = None
        self.halted = False
        self.halt_reason = None


class TestRiskEngineRollover(unittest.TestCase):
    def setUp(self):
        self.config = MagicMock()
        self.config.max_daily_loss_abs = 5000.0
        self.config.max_daily_loss_pct = 0.05
        self.config.max_drawdown_abs = 8000.0
        self.config.max_drawdown_pct = 0.08

        self.state = MockRiskState()
        self.logger = MagicMock()
        self.event_logger = MagicMock()

        self.engine = HardRiskEngine(
            risk_config=self.config,
            risk_state=self.state,
            logger=self.logger,
            event_logger=self.event_logger,
            trust_policy=True
        )

    def test_midnight_rollover(self):
        # Set trading day to yesterday
        yesterday = date.today() - timedelta(days=1)
        self.state.trading_day = yesterday
        self.state.equity_start = 100000.0
        self.state.equity_now = 98000.0
        self.state.max_equity_intraday = 100000.0
        self.state.min_equity_intraday = 98000.0
        self.state.realized_pnl_today = 1500.0
        self.state.halted = True
        self.state.halt_reason = "Yesterday halt"

        # Update heartbeat with today's date
        payload = {"equity": 98500.0, "realized_pnl_today": 0.0}
        self.engine.update_from_heartbeat(payload)

        # Verify rollover happened
        self.assertEqual(self.state.trading_day, date.today())
        self.assertEqual(self.state.equity_start, 98500.0)
        self.assertEqual(self.state.max_equity_intraday, 98500.0)
        self.assertEqual(self.state.min_equity_intraday, 98500.0)
        self.assertEqual(self.state.realized_pnl_today, 0.0)
        self.assertFalse(self.state.halted)
        self.assertIsNone(self.state.halt_reason)

    def test_soft_reset_deposit(self):
        # Initialize normal state
        self.state.trading_day = date.today()
        self.state.equity_start = 100000.0
        self.state.equity_now = 100000.0
        self.state.max_equity_intraday = 100000.0
        self.state.min_equity_intraday = 100000.0

        # Simulate a large deposit (e.g. 10,000, which is 10% change)
        payload = {"equity": 110000.0}
        self.engine.update_from_heartbeat(payload)

        # Verify equity_start is adjusted up by 10,000 (new: 110,000)
        self.assertEqual(self.state.equity_start, 110000.0)
        self.assertEqual(self.state.equity_now, 110000.0)
        self.assertEqual(self.state.max_equity_intraday, 110000.0)

        # Verify not halted
        self.assertFalse(self.state.halted)

    def test_soft_reset_withdrawal(self):
        # Initialize normal state
        self.state.trading_day = date.today()
        self.state.equity_start = 100000.0
        self.state.equity_now = 100000.0
        self.state.max_equity_intraday = 100000.0
        self.state.min_equity_intraday = 100000.0

        # Simulate a large withdrawal (e.g. 15,000, which is 15% change)
        payload = {"equity": 85000.0}
        self.engine.update_from_heartbeat(payload)

        # Verify equity_start is adjusted down by 15,000 (new: 85,000)
        self.assertEqual(self.state.equity_start, 85000.0)
        self.assertEqual(self.state.equity_now, 85000.0)
        self.assertEqual(self.state.min_equity_intraday, 85000.0)

        # Verify not halted
        self.assertFalse(self.state.halted)


if __name__ == "__main__":
    unittest.main()
