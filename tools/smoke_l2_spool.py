"""Smoke utility for L2 spooler."""

import gzip
import os
from pathlib import Path
import time

from MarketDataHub.config import L2Config
from MarketDataHub.models import L2Envelope
from MarketDataHub.spool.l2_spooler import L2Spooler


def _print_spool_files(spool_dir: Path) -> None:
    files = sorted(spool_dir.rglob("*.jsonl.gz"))
    if not files:
        print("No spool files created.")
        return
    for path in files:
        print("Spool file:", path)
        with gzip.open(path, "rb") as handle:
            print("  Sample line:", handle.readline().decode("utf-8").strip())


def main() -> None:
    config = L2Config()
    spooler = L2Spooler(config)
    try:
        for seq in range(3):
            envelope = L2Envelope(
                ts_ns=int(time.time_ns()),
                entity="fills",
                schema_ver=1,
                payload={"seq": seq, "side": "buy" if seq % 2 == 0 else "sell"},
            )
            spooler.append(envelope)
    finally:
        spooler.close()

    print("Spool directory:", config.spool_dir)
    _print_spool_files(Path(config.spool_dir))


if __name__ == "__main__":
    main()
