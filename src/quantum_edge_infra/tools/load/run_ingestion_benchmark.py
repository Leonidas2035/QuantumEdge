from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


def _bootstrap_paths() -> Path:
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    bot_root = root / "ai_scalper_bot"
    if bot_root.exists() and str(bot_root) not in sys.path:
        sys.path.insert(0, str(bot_root))
    return root


REPO_ROOT = _bootstrap_paths()

from bot.storage.event_bus import EventBus
from bot.storage.spooler import Spooler
from bot.storage.tsdb.questdb_ilp_writer import QuestDbIlpWriter
from tools.load.generate_market_events import MarketEventGenerator, SYMBOLS_DEFAULT


def _load_tsdb_defaults() -> Dict[str, Any]:
    path = REPO_ROOT / "config" / "tsdb.yaml"
    if not path.exists():
        return {}
    try:
        import yaml
    except Exception:
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _event_ts_seconds(event: Dict[str, Any]) -> Optional[float]:
    ts = event.get("ts_ms", event.get("ts"))
    if ts is None:
        return None
    try:
        ts_val = float(ts)
    except (TypeError, ValueError):
        return None
    if ts_val > 1e11:
        return ts_val / 1000.0
    return ts_val


class LagSampler:
    def __init__(self, max_samples: int = 10000, seed: int = 0) -> None:
        self.max_samples = max(int(max_samples), 100)
        self._rng = random.Random(seed)
        self._samples: List[float] = []

    def add(self, value: float) -> None:
        if value < 0:
            return
        if len(self._samples) < self.max_samples:
            self._samples.append(value)
            return
        idx = self._rng.randrange(0, self.max_samples)
        self._samples[idx] = value

    def summary(self) -> Dict[str, float]:
        if not self._samples:
            return {"p50_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0}
        data = sorted(self._samples)
        return {
            "p50_ms": data[int(0.5 * (len(data) - 1))],
            "p95_ms": data[int(0.95 * (len(data) - 1))],
            "max_ms": data[-1],
        }


class InstrumentedIlpWriter(QuestDbIlpWriter):
    def __init__(self, *args: Any, lag_sampler: LagSampler, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._lag_sampler = lag_sampler

    async def flush_events(self, events: Sequence[Dict[str, Any]]) -> bool:
        ok = await super().flush_events(events)
        if ok and events:
            ts_values = []
            for event in events:
                ts_val = _event_ts_seconds(event)
                if ts_val is not None:
                    ts_values.append(ts_val)
            if ts_values:
                lag_ms = max((time.time() - min(ts_values)) * 1000.0, 0.0)
                self._lag_sampler.add(lag_ms)
        return ok


@dataclass
class QueueSample:
    timestamp: float
    events: int
    bytes: int


def _summarize_samples(samples: Sequence[QueueSample]) -> Dict[str, float]:
    if not samples:
        return {
            "events_avg": 0.0,
            "events_max": 0.0,
            "bytes_avg": 0.0,
            "bytes_max": 0.0,
        }
    events = [s.events for s in samples]
    bytes_list = [s.bytes for s in samples]
    return {
        "events_avg": statistics.mean(events),
        "events_max": max(events),
        "bytes_avg": statistics.mean(bytes_list),
        "bytes_max": max(bytes_list),
    }


async def _run_benchmark(args: argparse.Namespace) -> Dict[str, Any]:
    defaults = _load_tsdb_defaults()
    questdb = defaults.get("questdb", {}) or {}
    queue_cfg = defaults.get("queue", {}) or {}
    spool_cfg = defaults.get("spool", {}) or {}
    retry_cfg = defaults.get("retry", {}) or {}

    ilp_http_url = args.ilp_http_url or questdb.get(
        "ilp_http_url",
        "http://127.0.0.1:9000/imp",
    )
    batch_rows = args.batch_rows or _int(
        defaults.get("write_batch_rows", 500),
        500,
    )
    flush_interval_ms = args.flush_interval_ms or _int(
        defaults.get("write_flush_interval_ms", 2000),
        2000,
    )
    queue_max_events = args.queue_max_events or _int(
        queue_cfg.get("max_events", 10000),
        10000,
    )
    queue_max_bytes = args.queue_max_bytes or _int(
        queue_cfg.get("max_bytes", 256 * 1024 * 1024),
        256 * 1024 * 1024,
    )
    drop_policy = (args.drop_policy or queue_cfg.get("drop_policy", "drop_lowest")).lower()

    bus = EventBus(
        max_events=queue_max_events,
        max_bytes=queue_max_bytes,
        drop_policy=drop_policy,
    )

    spooler = None
    if not args.no_spool:
        base_dir = spool_cfg.get("path", "runtime/spool")
        spooler = Spooler(
            base_dir=REPO_ROOT / base_dir,
            max_bytes=_int(
                spool_cfg.get("max_bytes", 1024 * 1024 * 1024),
                1024 * 1024 * 1024,
            ),
            retention_days=_int(spool_cfg.get("retention_days", 3), 3),
            max_file_bytes=_int(
                spool_cfg.get("max_file_bytes", 10 * 1024 * 1024),
                10 * 1024 * 1024,
            ),
            rotation_minutes=_int(spool_cfg.get("rotation_minutes", 5), 5),
        )

    lag_sampler = LagSampler(max_samples=args.lag_samples, seed=args.seed or 0)

    def _noop_transport(_: str) -> None:
        return None

    transport = _noop_transport if args.writer_mode == "noop" else None
    writer = InstrumentedIlpWriter(
        ilp_http_url=ilp_http_url,
        batch_rows=batch_rows,
        flush_interval_ms=flush_interval_ms,
        max_retries=_int(retry_cfg.get("max_retries", 5), 5),
        base_backoff_ms=_int(retry_cfg.get("base_backoff_ms", 200), 200),
        max_backoff_ms=_int(retry_cfg.get("max_backoff_ms", 5000), 5000),
        spooler=spooler,
        transport=transport,
        lag_sampler=lag_sampler,
    )

    generator = MarketEventGenerator(
        symbols=args.symbols,
        trades_per_sec=args.trades_per_sec,
        l1_per_sec=args.l1_per_sec,
        seed=args.seed,
    )

    gen_stop = asyncio.Event()
    writer_stop = asyncio.Event()
    writer_task = asyncio.create_task(writer.run(bus, writer_stop))
    gen_task = asyncio.create_task(generator.run(bus, gen_stop))

    samples: List[QueueSample] = []
    start = time.time()
    while time.time() - start < args.duration_sec:
        snapshot = bus.snapshot()
        samples.append(
            QueueSample(
                timestamp=time.time(),
                events=snapshot["events"],
                bytes=snapshot["bytes"],
            )
        )
        await asyncio.sleep(args.sample_interval_sec)

    gen_stop.set()
    await gen_task

    drain_start = time.time()
    while time.time() - drain_start < args.drain_sec:
        snapshot = bus.snapshot()
        if snapshot["events"] == 0:
            break
        await asyncio.sleep(args.sample_interval_sec)

    writer_stop.set()
    await writer_task

    elapsed = time.time() - start
    queue_summary = _summarize_samples(samples)
    lag_summary = lag_sampler.summary()

    summary: Dict[str, Any] = {
        "duration_sec": round(elapsed, 3),
        "symbols": args.symbols,
        "trades_per_sec_per_symbol": args.trades_per_sec,
        "l1_per_sec_per_symbol": args.l1_per_sec,
        "writer_mode": args.writer_mode,
        "batch_rows": batch_rows,
        "flush_interval_ms": flush_interval_ms,
        "queue_max_events": queue_max_events,
        "queue_max_bytes": queue_max_bytes,
        "drop_policy": drop_policy,
        "events_generated": generator.stats.trades + generator.stats.l1_updates,
        "events_published": bus.stats.published,
        "events_dropped": bus.stats.dropped,
        "writer_batches": writer.stats.batches,
        "writer_events": writer.stats.events,
        "writer_failures": writer.stats.failures,
        "writer_rows_per_sec": round(writer.stats.events / elapsed, 2) if elapsed > 0 else 0.0,
        "spool_events": spooler.stats.events if spooler else 0,
        "spool_batches": spooler.stats.batches if spooler else 0,
        "spool_bytes": spooler.stats.bytes_written if spooler else 0,
        "queue_events_avg": round(queue_summary["events_avg"], 2),
        "queue_events_max": queue_summary["events_max"],
        "queue_bytes_avg": round(queue_summary["bytes_avg"], 2),
        "queue_bytes_max": queue_summary["bytes_max"],
        "lag_p50_ms": round(lag_summary["p50_ms"], 2),
        "lag_p95_ms": round(lag_summary["p95_ms"], 2),
        "lag_max_ms": round(lag_summary["max_ms"], 2),
    }
    return summary


def _print_summary(summary: Dict[str, Any]) -> None:
    lines = [
        "QuestDB ILP ingest benchmark",
        f"duration_sec: {summary['duration_sec']}",
        f"writer_mode: {summary['writer_mode']}",
        f"events_generated: {summary['events_generated']}",
        f"events_published: {summary['events_published']}",
        f"events_dropped: {summary['events_dropped']}",
        f"writer_rows_per_sec: {summary['writer_rows_per_sec']}",
        f"writer_batches: {summary['writer_batches']}",
        f"writer_failures: {summary['writer_failures']}",
        f"queue_events_avg: {summary['queue_events_avg']}",
        f"queue_events_max: {summary['queue_events_max']}",
        f"queue_bytes_avg: {summary['queue_bytes_avg']}",
        f"queue_bytes_max: {summary['queue_bytes_max']}",
        f"lag_p50_ms: {summary['lag_p50_ms']}",
        f"lag_p95_ms: {summary['lag_p95_ms']}",
        f"lag_max_ms: {summary['lag_max_ms']}",
        f"spool_events: {summary['spool_events']}",
        f"spool_bytes: {summary['spool_bytes']}",
    ]
    print("\n".join(lines))


def main() -> int:
    defaults = _load_tsdb_defaults()
    questdb = defaults.get("questdb", {}) or {}

    parser = argparse.ArgumentParser(description="Run QuestDB ILP ingestion benchmark.")
    parser.add_argument("--duration-sec", type=float, default=20.0, help="Benchmark duration.")
    parser.add_argument("--drain-sec", type=float, default=5.0, help="Drain time after generator stops.")
    parser.add_argument("--sample-interval-sec", type=float, default=0.5, help="Queue sample interval.")
    parser.add_argument("--trades-per-sec", type=float, default=20.0, help="Trades/sec per symbol.")
    parser.add_argument("--l1-per-sec", type=float, default=20.0, help="L1 updates/sec per symbol.")
    parser.add_argument("--symbols", nargs="+", default=None)
    parser.add_argument("--seed", type=int, default=7, help="Random seed.")
    parser.add_argument("--writer-mode", choices=["questdb", "noop"], default="questdb", help="Writer target.")
    parser.add_argument(
        "--ilp-http-url",
        default=questdb.get("ilp_http_url", "http://127.0.0.1:9000/imp"),
        help="QuestDB ILP HTTP URL.",
    )
    parser.add_argument("--batch-rows", type=int, help="Override batch rows.")
    parser.add_argument("--flush-interval-ms", type=int, help="Override flush interval (ms).")
    parser.add_argument("--queue-max-events", type=int, help="Override queue max events.")
    parser.add_argument("--queue-max-bytes", type=int, help="Override queue max bytes.")
    parser.add_argument("--drop-policy", choices=["drop_lowest", "drop_newest"], help="Queue drop policy.")
    parser.add_argument("--no-spool", action="store_true", help="Disable spooler.")
    parser.add_argument("--lag-samples", type=int, default=10000, help="Max lag samples to keep.")
    parser.add_argument("--out", help="Write summary JSON to this path.")
    args = parser.parse_args()

    if args.symbols and len(args.symbols) == 1 and "," in args.symbols[0]:
        args.symbols = [item.strip().upper() for item in args.symbols[0].split(",") if item.strip()]
    if not args.symbols:
        args.symbols = SYMBOLS_DEFAULT

    summary = asyncio.run(_run_benchmark(args))
    _print_summary(summary)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"[benchmark] Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
