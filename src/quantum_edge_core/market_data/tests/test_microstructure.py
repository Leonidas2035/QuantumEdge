import pytest

from quantum_edge_core.market_data.analytics.microstructure import (
    MicrostructureAnalyzer,
)
from quantum_edge_core.events import OrderBookUpdate


def test_detect_liquidity_walls():
    analyzer = MicrostructureAnalyzer()

    # Mock order book with a clear wall at 70000
    bids = [
        [69900.0, 1.0],
        [69800.0, 1.5],
        [69700.0, 1.2],
        [69600.0, 20.0],  # Wall
        [69500.0, 1.0],
    ]
    asks = [
        [70100.0, 1.0],
        [70200.0, 1.5],
        [70300.0, 1.2],
        [70400.0, 1.0],
        [70500.0, 1.0],
    ]
    book = {"bids": bids, "asks": asks}

    # This method doesn't exist yet, we will implement it
    walls = analyzer.detect_liquidity_walls(book, avg_volume_multiplier=3.0)

    assert len(walls) >= 1
    assert any(w["price"] == 69600.0 for w in walls)
