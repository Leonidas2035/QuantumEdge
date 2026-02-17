import asyncio

from bot.storage.event_bus import EventBus
from bot.storage.spooler import Spooler
from bot.storage.tsdb.questdb_ilp_writer import QuestDbIlpWriter


def test_ilp_writer_flush_on_batch():
    async def _run():
        sent = []

        def transport(payload: str) -> None:
            sent.append(payload)

        bus = EventBus(max_events=10, max_bytes=4096)
        writer = QuestDbIlpWriter(
            ilp_http_url="http://127.0.0.1:9000/imp",
            batch_rows=2,
            flush_interval_ms=10000,
            max_retries=0,
            transport=transport,
        )
        stop = asyncio.Event()
        task = asyncio.create_task(writer.run(bus, stop))
        await bus.publish(
            {
                "table": "market_l1",
                "symbol": "BTCUSDT",
                "bid": 1.0,
                "ask": 2.0,
                "bid_sz": 1.0,
                "ask_sz": 1.0,
                "ts": 1,
            }
        )
        await bus.publish(
            {
                "table": "market_l1",
                "symbol": "BTCUSDT",
                "bid": 1.1,
                "ask": 2.1,
                "bid_sz": 1.0,
                "ask_sz": 1.0,
                "ts": 2,
            }
        )
        await asyncio.sleep(0.05)
        stop.set()
        await task
        assert len(sent) == 1

    asyncio.run(_run())


def test_ilp_writer_spools_on_failure(tmp_path):
    async def _run():
        def transport(_: str) -> None:
            raise RuntimeError("fail")

        spooler = Spooler(
            base_dir=tmp_path,
            max_bytes=10 * 1024 * 1024,
            retention_days=1,
            max_file_bytes=1024,
            rotation_minutes=1,
        )
        writer = QuestDbIlpWriter(
            ilp_http_url="http://127.0.0.1:9000/imp",
            batch_rows=1,
            flush_interval_ms=1000,
            max_retries=0,
            spooler=spooler,
            transport=transport,
        )
        ok = await writer.flush_events(
            [
                {
                    "table": "market_l1",
                    "symbol": "BTCUSDT",
                    "bid": 1.0,
                    "ask": 2.0,
                    "bid_sz": 1.0,
                    "ask_sz": 1.0,
                    "ts": 1,
                }
            ]
        )
        assert not ok
        assert list(tmp_path.rglob("*.jsonl.gz"))

    asyncio.run(_run())
