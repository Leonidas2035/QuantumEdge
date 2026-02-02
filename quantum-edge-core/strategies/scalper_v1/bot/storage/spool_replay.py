from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
from typing import Dict, Iterable, List

from bot.storage.tsdb.questdb_ilp_writer import QuestDbIlpWriter
from bot.storage.tsdb_config import load_tsdb_config


def iter_spool_files(base_dir: Path) -> Iterable[Path]:
    return sorted(base_dir.rglob("*.jsonl.gz"))


def load_events(path: Path) -> Iterable[Dict]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue


async def replay_events(spool_dir: Path, batch_rows: int, writer: QuestDbIlpWriter) -> int:
    sent = 0
    batch: List[Dict] = []
    for file_path in iter_spool_files(spool_dir):
        for event in load_events(file_path):
            batch.append(event)
            if len(batch) >= batch_rows:
                ok = await writer.flush_events(batch)
                if ok:
                    sent += len(batch)
                batch = []
    if batch:
        ok = await writer.flush_events(batch)
        if ok:
            sent += len(batch)
    return sent


def main() -> None:
    cfg = load_tsdb_config()
    parser = argparse.ArgumentParser(description="Replay TSDB spool files into QuestDB ILP.")
    parser.add_argument("--spool-dir", default=str(cfg.spool.path), help="Path to spool directory.")
    parser.add_argument("--batch-rows", type=int, default=cfg.writer.batch_rows, help="Rows per flush.")
    parser.add_argument("--ilp-http-url", default=cfg.questdb.ilp_http_url, help="QuestDB ILP HTTP URL.")
    args = parser.parse_args()

    writer = QuestDbIlpWriter(
        ilp_http_url=args.ilp_http_url,
        batch_rows=max(args.batch_rows, 1),
        flush_interval_ms=1000,
        max_retries=cfg.retry.max_retries,
        base_backoff_ms=cfg.retry.base_backoff_ms,
        max_backoff_ms=cfg.retry.max_backoff_ms,
        spooler=None,
    )
    spool_dir = Path(args.spool_dir)
    if not spool_dir.exists():
        raise SystemExit(f"Spool directory not found: {spool_dir}")
    import asyncio

    sent = asyncio.run(replay_events(spool_dir, args.batch_rows, writer))
    print(f"[spool_replay] Replayed events: {sent}")


if __name__ == "__main__":
    main()
