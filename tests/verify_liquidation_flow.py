"""
Verification script for Liquidation Data Integration.
Simulates:
1. LiquidationFeed parsing (Mock)
2. QuestDB ILP Formatting
3. Context Accumulation & Feature Calculation
4. Snapshot Generation
"""

import asyncio
import time
import json
from quantum_edge_core.market_data.feeds.liquidations import LiquidationFeed
from quantum_edge_core.market_data.config import HubConfig
from quantum_edge_core.market_data.bus.event_bus import EventBus
from quantum_edge_core.market_data.tsdb.quest_writer import QuestILPWriter
from quantum_edge_core.supervisor.context.builder import ContextBuilder


# Mock Event Bus
class MockBus(EventBus):
    async def publish(self, event):
        print(
            f"[BUS] Published: {event['event_type']} {event.get('symbol')} ${event.get('usd_size', 0):.2f}"
        )


async def verify():
    print("--- 1. Testing Feed Logic (Parsing) ---")
    config = HubConfig.load()
    bus = MockBus()
    feed = LiquidationFeed(config, bus)

    # Simulate Binance Payload
    mock_payload = {
        "e": "forceOrder",
        "o": {
            "s": "BTCUSDT",
            "S": "SELL",
            "q": "0.5",
            "p": "65000.00",
            "ap": "64950.00",
            "T": int(time.time() * 1000),
        },
    }
    await feed._handle_message(json.dumps(mock_payload))

    print("\n--- 2. Testing QuestDB Writer (ILP) ---")
    writer = QuestILPWriter()
    # Test Enqueue with timestamp
    ts_ns = time.time_ns()
    ilp = writer._format_ilp(
        "liquidations",
        {"symbol": "BTCUSDT", "side": "SELL"},
        {"price": 64950.00, "qty": 0.5, "usd_size": 32475.00},
        timestamp_ns=ts_ns,
    )
    print(f"[ILP] {ilp.strip()}")
    assert str(ts_ns) in ilp

    print("\n--- 3. Testing Supervisor Aggregation ---")
    builder = ContextBuilder()
    acc = builder.get_accumulator("BTCUSDT")

    # Inject liquidations
    liq_event_1 = {
        "symbol": "BTCUSDT",
        "side": "SELL",
        "usd_size": 100000.0,
        "timestamp": time.time() * 1000,
    }
    liq_event_2 = {
        "symbol": "BTCUSDT",
        "side": "BUY",
        "usd_size": 25000.0,
        "timestamp": time.time() * 1000,
    }

    builder.on_market_data("liquidation", liq_event_1)
    builder.on_market_data("liquidation", liq_event_2)

    snapshot = builder.build_snapshot("BTCUSDT")
    metrics = snapshot["microstructure"].get("liquidation_pressure", {})
    print(f"[METRICS] Buy Vol: {metrics.get('liq_buy_vol_1m')}")
    print(f"[METRICS] Sell Vol: {metrics.get('liq_sell_vol_1m')}")
    print(f"[METRICS] Net: {metrics.get('liq_net_1m')}")

    assert metrics.get("liq_sell_vol_1m") == 100000.0
    assert metrics.get("liq_buy_vol_1m") == 25000.0
    assert metrics.get("liq_net_1m") == -75000.0
    print("✅ Verification Passed")


if __name__ == "__main__":
    asyncio.run(verify())
