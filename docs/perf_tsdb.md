# TSDB Performance Tuning (QuestDB + ILP)

This guide captures the tuning knobs and a repeatable load test for the QuestDB ILP pipeline under a 16GB RAM budget.

## Key tuning knobs

Edit `config/tsdb.yaml`:
- `write_batch_rows`: ILP batch size (rows). Larger batches improve throughput, increase latency.
- `write_flush_interval_ms`: max time before flushing a partial batch.
- `queue.max_events` / `queue.max_bytes`: EventBus backpressure limits.
- `queue.drop_policy`: `drop_lowest` (default) or `drop_newest`.
- `memory_budgets.hot_ring_bytes` / `memory_budgets.hot_ring_minutes`: in-memory hot data window.
- `spool.*`: disk spill settings when QuestDB is unavailable.

## Recommended starting settings (16GB)

These are conservative starting points that keep memory stable on 16GB systems:

| Setting | Value | Notes |
| --- | --- | --- |
| `write_batch_rows` | 1000 | Increase to 2000+ for higher throughput. |
| `write_flush_interval_ms` | 500 | Reduce to 250ms for lower latency. |
| `queue.max_events` | 50000 | Increase if bursts cause drops. |
| `queue.max_bytes` | 536870912 | 512MB queue cap. |
| `queue.drop_policy` | `drop_lowest` | Drops raw trades before L2 events. |
| `memory_budgets.hot_ring_bytes` | 536870912 | 512MB hot ring. |
| `memory_budgets.hot_ring_minutes` | 10 | Keep only the last N minutes hot. |
| `spool.max_bytes` | 2147483648 | 2GB spool cap. |

Adjust upward only after verifying QuestDB is healthy (see below).

## Load testing workflow

Synthetic generator only (no QuestDB):

```bash
python tools/load/generate_market_events.py --duration-sec 10 --trades-per-sec 20 --l1-per-sec 20 --drain
```

Benchmark with a noop writer (CI-safe):

```bash
python tools/load/run_ingestion_benchmark.py --duration-sec 20 --writer-mode noop --trades-per-sec 20 --l1-per-sec 20 --no-spool
```

Benchmark against QuestDB ILP:

```bash
python tools/load/run_ingestion_benchmark.py --duration-sec 60 --writer-mode questdb --trades-per-sec 50 --l1-per-sec 50
```

The benchmark prints queue depth, throughput (rows/sec), and end-to-end lag (p50/p95).

## Verifying QuestDB health + ingestion

Health endpoint:

```bash
curl http://127.0.0.1:9003/health
```

Quick ingest sanity check:

```bash
curl "http://127.0.0.1:9000/exec?query=select%20count()%20from%20market_l1"
```

If queue depth or lag grows, reduce event rates or increase `queue.max_*` and `write_batch_rows` gradually.
