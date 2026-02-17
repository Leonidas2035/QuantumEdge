import asyncio

from bot.storage.event_bus import EventBus, EventPriority


def test_event_bus_drop_policy():
    async def _run():
        bus = EventBus(max_events=2, max_bytes=4096)
        await bus.publish(
            {
                "table": "market_l1",
                "symbol": "BTCUSDT",
                "bid": 1.0,
                "ask": 2.0,
                "bid_sz": 1.0,
                "ask_sz": 1.0,
                "ts": 1,
            },
            priority=EventPriority.LOW,
        )
        await bus.publish(
            {
                "table": "market_l1",
                "symbol": "ETHUSDT",
                "bid": 1.0,
                "ask": 2.0,
                "bid_sz": 1.0,
                "ask_sz": 1.0,
                "ts": 2,
            },
            priority=EventPriority.LOW,
        )
        ok = await bus.publish(
            {
                "table": "signals",
                "bot_id": "b1",
                "symbol": "BTCUSDT",
                "signal": "long",
                "score": 0.1,
                "ts": 3,
            },
            priority=EventPriority.HIGH,
        )
        assert ok
        snapshot = bus.snapshot()
        assert snapshot["events"] == 2
        items = await bus.drain(2)
        assert any(item.get("table") == "signals" for item in items)

    asyncio.run(_run())
