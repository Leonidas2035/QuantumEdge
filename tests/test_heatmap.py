"""
Tests for LiquidationHeatmap Logic.
"""

import time
import unittest
from quantum_edge_core.supervisor.context.heatmap import LiquidationHeatmap


class TestHeatmap(unittest.TestCase):
    def test_bucketing_and_aggregation(self):
        hm = LiquidationHeatmap(bin_size=10.0)

        # Add events in same bucket (65000)
        hm.on_liquidation({"price": 65004.0, "usd_size": 100.0, "side": "SELL", "timestamp": time.time() * 1000})
        hm.on_liquidation({"price": 64996.0, "usd_size": 50.0, "side": "SELL", "timestamp": time.time() * 1000})

        # Add event in different bucket (65010)
        hm.on_liquidation({"price": 65014.0, "usd_size": 200.0, "side": "BUY", "timestamp": time.time() * 1000})

        top = hm.get_top_clusters(n=5)

        # Sort by price for deterministic assertion if volumes were equal, but here volumes differ
        # Expected:
        # 1. 65010 (200.0)
        # 2. 65000 (150.0)

        self.assertEqual(len(top), 2)
        self.assertEqual(top[0]["price"], 65010.0)
        self.assertEqual(top[0]["vol"], 200.0)
        self.assertEqual(top[0]["bias"], "BUY")

        self.assertEqual(top[1]["price"], 65000.0)
        self.assertEqual(top[1]["vol"], 150.0)

    def test_pruning(self):
        hm = LiquidationHeatmap(bin_size=10.0, retention_window_s=1)  # 1 second window

        # Event 1: Now
        now = time.time()
        hm.on_liquidation({"price": 65000.0, "usd_size": 100.0, "side": "SELL", "timestamp": now * 1000})

        # Verify it's there
        self.assertEqual(len(hm.get_top_clusters()), 1)

        # Move time forward 2 seconds
        # We need to mock time or pass explicit current_ts to prune
        future_ns = int((now + 2) * 1_000_000_000)
        hm.prune(current_ts_ns=future_ns)

        # Verify it's gone
        self.assertEqual(len(hm.get_top_clusters()), 0)


if __name__ == "__main__":
    unittest.main()
