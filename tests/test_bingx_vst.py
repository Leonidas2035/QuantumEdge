import os
import sys

import pytest


@pytest.mark.integration
def test_bingx_vst_live_order():
    if os.getenv("RUN_LIVE_VST_TESTS") != "1":
        pytest.skip("Live BingX VST test requires RUN_LIVE_VST_TESTS=1")

    import ccxt

    if "/home/layman/QuantumEdge/src" not in sys.path:
        sys.path.insert(0, "/home/layman/QuantumEdge/src")
    from quantum_edge_core.dyn_dca_bot.main import _load_secrets

    _load_secrets()

    api_key = os.getenv("BINGX_DEMO_API_KEY", "")
    api_secret = os.getenv("BINGX_DEMO_API_SECRET", "")

    exchange = ccxt.bingx(
        {
            "apiKey": api_key,
            "secret": api_secret,
            "options": {"defaultType": "swap"},
        }
    )
    exchange.set_sandbox_mode(True)

    order = exchange.create_order(
        symbol="BTC/USDT:USDT",
        type="limit",
        side="buy",
        amount=0.01,
        price=60000.0,
        params={"positionSide": "LONG"},
    )
    assert "id" in order
