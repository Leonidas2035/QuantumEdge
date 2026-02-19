from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import zmq

from quantum_edge_core.ai_scalper_bot.bot.core.models import MarketTick
from quantum_edge_core.ai_scalper_bot.bot.core.orderbook import OrderBookCache
from quantum_edge_core.ai_scalper_bot.bot.infrastructure.zmq_adapter import \
    ZmqSubStream


def test_models_slots():
    """Verify that models are using slots for memory optimization."""
    tick = MarketTick(100.0, 1.0, 1234567890.0, True)
    # If slots are used, __dict__ should not exist
    assert not hasattr(tick, "__dict__")
    assert tick.price == 100.0


def test_orderbook_cache_update_and_snapshot():
    """Test O(1) update and snapshot generation."""
    cache = OrderBookCache(history_len=5)

    # Tick 1
    tick1 = {"p": "50000.0", "q": "0.1", "T": 1600000000, "m": True}
    cache.update(tick1)

    # Tick 2
    tick2 = {"p": "50001.0", "q": "0.2", "T": 1600000001, "m": False}
    cache.update(tick2)

    snapshot = cache.get_snapshot()

    assert isinstance(snapshot, np.ndarray)
    assert snapshot.shape == (2, 3)  # 2 ticks, 3 columns [p, q, m]

    # Verify content (Tick 1)
    assert snapshot[0][0] == 50000.0
    assert snapshot[0][1] == 0.1
    assert snapshot[0][2] == 1.0  # True -> 1.0

    # Verify content (Tick 2)
    assert snapshot[1][0] == 50001.0
    assert snapshot[1][2] == 0.0  # False -> 0.0


def test_orderbook_cache_rolling_window():
    """Test that deque maintains history length."""
    cache = OrderBookCache(history_len=2)

    cache.update({"p": 1, "q": 1})
    cache.update({"p": 2, "q": 1})
    cache.update({"p": 3, "q": 1})

    snapshot = cache.get_snapshot()
    assert len(snapshot) == 2
    assert snapshot[0][0] == 2.0
    assert snapshot[1][0] == 3.0


def test_zmq_adapter_decoding():
    """Test ZMQ adapter JSON decoding and error handling."""
    with patch("zmq.Context") as mock_ctx:
        mock_socket = MagicMock()
        mock_ctx.return_value.socket.return_value = mock_socket

        adapter = ZmqSubStream("tcp://test")

        # Test Success
        mock_socket.recv_multipart.return_value = [b"topic", b'{"p": 100, "q": 1}']
        tick = adapter.get_latest_tick(timeout_ms=10)
        assert tick["p"] == 100

        # Test Malformed
        mock_socket.recv_multipart.return_value = [b"topic", b"BAD_JSON"]
        tick = adapter.get_latest_tick(timeout_ms=10)
        assert tick is None  # Should return None and log warning, not crash

        # Test Empty/Timeout
        mock_socket.recv_multipart.side_effect = zmq.Again
        tick = adapter.get_latest_tick(timeout_ms=0)
        assert tick is None

        # Test Fewer than 2 frames
        mock_socket.recv_multipart.side_effect = None
        mock_socket.recv_multipart.return_value = [b"only_topic"]
        tick = adapter.get_latest_tick(timeout_ms=10)
        assert tick is None


if __name__ == "__main__":
    pytest.main([__file__])
