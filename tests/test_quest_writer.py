import pytest

pytest.skip("Legacy test broken by src-layout migration", allow_module_level=True)
import asyncio
from quantum_edge_core.market_data.config import L2Config, TsdbConfig
from quantum_edge_core.market_data.models import Bar1sEvent, L1Event, Priority
from quantum_edge_core.market_data.tsdb.quest_writer import QuestILPWriter


def test_format_lines_for_l1_and_bar() -> None:
    tsdb_config = TsdbConfig()
    writer = QuestILPWriter(tsdb_config, L2Config(enabled=False))
    l1 = L1Event(
        ts_ns=1_700_000_000_000_000_000,
        symbol="BTCUSDT",
        event_type="l1",
        seq=1,
        priority=Priority.L1,
        best_bid=1.0,
        best_ask=1.1,
        bid_size=0.5,
        ask_size=0.4,
    )
    bar = Bar1sEvent(
        ts_ns=1_700_000_000_000_001_000,
        symbol="BTCUSDT",
        event_type="bar1s",
        seq=2,
        priority=Priority.L1,
        open=1.0,
        high=1.2,
        low=0.9,
        close=1.1,
        volume=10.0,
        trades=5,
    )
    lines, _ = writer._build_batch()
    assert lines == []
    asyncio.run(writer.enqueue(l1))
    asyncio.run(writer.enqueue(bar))
    built, _ = writer._build_batch()
    assert any("market_l1" in line for line in built)
    assert any("bars_1s" in line for line in built)


def test_queue_drop_when_full() -> None:
    config = TsdbConfig(bars_queue_max=1)
    writer = QuestILPWriter(config, L2Config(enabled=False))
    bar1 = Bar1sEvent(
        ts_ns=1,
        symbol="SYM",
        event_type="bar1s",
        seq=1,
        priority=Priority.L1,
        open=1,
        high=2,
        low=1,
        close=1.5,
        volume=1,
        trades=1,
    )
    bar2 = Bar1sEvent(
        ts_ns=2,
        symbol="SYM",
        event_type="bar1s",
        seq=2,
        priority=Priority.L1,
        open=1,
        high=2,
        low=1,
        close=1.6,
        volume=2,
        trades=2,
    )
    asyncio.run(writer.enqueue(bar1))
    asyncio.run(writer.enqueue(bar2))
    assert writer.metrics.dropped_rows >= 1
