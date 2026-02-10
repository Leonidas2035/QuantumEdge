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

## Resource profiles and CPU pinning

The hot path is Hub → ZeroMQ → Bots, while the warm path is the Hub → QuestDB ILP ingestion (QuestDB is capped at 16 GiB resident memory). Treat the Hub as a pure data-plane service and isolate it from noisy neighbors. Run `bash tools/perf/cpu_layout.sh` to get a live list of P-core/E-core CPU IDs; plug the suggested ranges into the commands below.

### Profile: 2 bots / few pairs (MVP)

| Component | RAM budget | Notes |
| --- | --- | --- |
| QuestDB (Docker) | 16 GiB | Enforce via `--memory=16g` so ILP queues never consume >16 GiB. |
| OS page cache + shared services | 8‑10 GiB | Leave headroom for kernel caches, Supervisor, telemetry, monitoring. |
| Hub (meta_agent) | 2 GiB | Pin to dedicated P-cores; keep heap slim so latency stays predictable. |
| Bots (each) | 1 GiB | E-core affinity + `nice +5` keeps execution soft. |
| Supervisor + tooling | 0.5 GiB | Pin to a tertiary core; do not overlap with Hub or bot ranges. |
| Headroom | ≥1 GiB | Reserve for bursts, future gating, and errant memory spikes. |

| Component | CPU pin example | Notes |
| --- | --- | --- |
| Hub | `taskset -c 0-1` | P-core pair only; combine with `nice -n -5` (or `chrt --rr 50` if you have a gating policy, but avoid hard RT without approval). |
| Bots | `taskset -c 4,5` and `taskset -c 6,7` | Two bots on separate E-core pairs. |
| QuestDB | `--cpuset-cpus="10-13"` | Run the Docker container on E-cores away from Hub/bots. |
| Supervisor | `taskset -c 2` | Keeps control-plane work out of the hot path. |

#### Supervisor-managed commands

Use a systemd unit or Supervisor drop-in that wraps each service with `taskset`/`nice`.

```ini
[Service]
ExecStart=/bin/bash -c 'taskset -c 0,1 nice -n -5 ./scripts/linux/run.sh meta --config /opt/QuantumEdge/config/meta_agent.yaml'
ExecStartPost=/bin/bash -c 'taskset -c 4,5 nice -n 5 ./scripts/linux/run_bot.sh --config /opt/QuantumEdge/config/bot.yaml'
ExecStartPost=/bin/bash -c 'taskset -c 6,7 nice -n 5 ./scripts/linux/run_bot.sh --config /opt/QuantumEdge/config/bot.yaml'
ExecStartPost=/bin/bash -c 'docker run --rm --cpuset-cpus="10-13" --memory=16g --name questdb -p 9000:9000 -p 9003:9003 questdb/questdb:latest'
```

#### Direct commands

```bash
taskset -c 0,1 nice -n -5 ./scripts/linux/run.sh meta --config config/meta_agent.yaml
taskset -c 4,5 nice -n 5 ./scripts/linux/run_bot.sh --config config/bot.yaml
taskset -c 6,7 nice -n 5 ./scripts/linux/run_bot.sh --config config/bot.yaml
docker run --rm \
  --name questdb \
  --cpuset-cpus="10-13" \
  --memory=16g \
  -p 9000:9000 \
  -p 9003:9003 \
  questdb/questdb:latest
```

### Profile: 10 pairs / 4 bots (scale)

| Component | RAM budget | Notes |
| --- | --- | --- |
| QuestDB (Docker) | 16 GiB | ILP pipeline still limited to 16 GiB. |
| OS cache + shared services | 6‑8 GiB | Reduced headroom once four bots are running. |
| Hub | 2 GiB | Keep pinned to the same P-core pair (0-1) with minimal heap. |
| Bots (4) | 4‑6 GiB | Each bot ~1.2‑1.5 GiB; pin two bots per E-core quartet. |
| Supervisor | 0.5 GiB | Keep on a spare core (e.g., `taskset -c 3`). |
| Headroom | ≥1 GiB | Reserve for gating, monitoring, and scheduler activity. |

| Component | CPU pin example | Notes |
| --- | --- | --- |
| Hub | `taskset -c 0-1` | Leave P-core `2` idle or for bursts (e.g., gating/approval). |
| Bots | `taskset -c 4-7`, `taskset -c 8-11` | Spread 4 bots across two E-core quartets. |
| QuestDB | `--cpuset-cpus="12-15"` | Dedicate a separate E-core region. |
| Supervisor | `taskset -c 3` | Keeps logging/control-plane off P/E intersections. |

#### Supervisor-managed commands

```ini
[Service]
ExecStart=/bin/bash -c 'taskset -c 0,1 nice -n -5 ./scripts/linux/run.sh meta --config /opt/QuantumEdge/config/meta_agent.yaml'
ExecStartPost=/bin/bash -c 'taskset -c 4-7 nice -n 5 ./scripts/linux/run_bot.sh --config /opt/QuantumEdge/config/bot.yaml'
ExecStartPost=/bin/bash -c 'taskset -c 8-11 nice -n 5 ./scripts/linux/run_bot.sh --config /opt/QuantumEdge/config/bot.yaml'
ExecStartPost=/bin/bash -c 'taskset -c 12-15 nice -n 5 ./scripts/linux/run_bot.sh --config /opt/QuantumEdge/config/bot.yaml'
ExecStartPost=/bin/bash -c 'docker run --rm --cpuset-cpus="16-19" --memory=16g --name questdb -p 9000:9000 -p 9003:9003 questdb/questdb:latest'
```

#### Direct commands

```bash
taskset -c 0,1 nice -n -5 ./scripts/linux/run.sh meta --config config/meta_agent.yaml
taskset -c 4-7 nice -n 5 ./scripts/linux/run_bot.sh --config config/bot.yaml
taskset -c 8-11 nice -n 5 ./scripts/linux/run_bot.sh --config config/bot.yaml
taskset -c 12-15 nice -n 5 ./scripts/linux/run_bot.sh --config config/bot.yaml
docker run --rm \
  --name questdb \
  --cpuset-cpus="16-19" \
  --memory=16g \
  -p 9000:9000 \
  -p 9003:9003 \
  questdb/questdb:latest
```

Keep the Hub → bot ZeroMQ pipeline on isolated cores and pin QuestDB away from them; mixing P/E workloads causes jitter.
