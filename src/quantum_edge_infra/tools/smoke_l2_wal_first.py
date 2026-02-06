"""Smoke utility that demonstrates WAL-first L2 ingestion even when ILP fails."""

import asyncio
import time
from pathlib import Path

from market_data.config import L2Config, TsdbConfig
from market_data.models import L2Envelope
from market_data.tsdb.quest_writer import QuestILPWriter


async def _produce(writer: QuestILPWriter) -> None:
    await writer.start()
    try:
        for i in range(3):
            event = L2Envelope(
                ts_ns=int(time.time_ns()),
                entity="fills",
                symbol="BTCUSDT",
                schema_ver=1,
                payload={"seq": i, "side": "buy" if i % 2 == 0 else "sell"},
            )
            await writer.enqueue_l2(event)
            await asyncio.sleep(0.1)
    finally:
        await writer.stop()


def main() -> None:
    tsdb_config = TsdbConfig(host="127.0.0.1", ilp_port=9999, batch_rows=1, flush_interval_ms=100)
    l2_config = L2Config()
    writer = QuestILPWriter(tsdb_config, l2_config)
    asyncio.run(_produce(writer))
    spool_dir = Path(l2_config.spool_dir)
    print("Spool directory:", spool_dir)
    for path in sorted(spool_dir.rglob("*.jsonl.gz")):
        print("  -", path)
        with path.open("rb") as handle:
            print("    Sample:", handle.read(200))


if __name__ == "__main__":
    main()
