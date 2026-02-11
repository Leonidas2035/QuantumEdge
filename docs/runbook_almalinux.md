# AlmaLinux Runbook (QuantumEdge)

## Prereqs

- AlmaLinux 9.x
- Python 3.11+ (recommended)
- Git

## Install system dependencies

```bash
sudo dnf install -y python3 python3-venv python3-devel gcc gcc-c++ make git
```

## Clone location (recommended)

```bash
sudo mkdir -p /opt/QuantumEdge
sudo chown -R "$USER:$USER" /opt/QuantumEdge
git clone https://github.com/Leonidas2035/QuantumEdge.git /opt/QuantumEdge
cd /opt/QuantumEdge
```

## Setup (venv + deps)

```bash
./scripts/linux/setup.sh
```

## Environment file (optional)

Create `/etc/quantumedge/env` (not tracked in git):

```bash
sudo mkdir -p /etc/quantumedge
sudo nano /etc/quantumedge/env
```

Example (placeholders only):

```
QE_ROOT=/opt/QuantumEdge
SUPERVISOR_HOST=127.0.0.1
SUPERVISOR_PORT=8765
BINANCE_MODE=paper
BINANCE_USE_TESTNET=1
BINANCE_API_KEY=REPLACE_ME
BINANCE_API_SECRET=REPLACE_ME
```

## Run foreground (canonical)

```bash
./scripts/linux/run_supervisor.sh run-foreground --episode-set smoke --scenario-id S00
```

Equivalent direct command:

```bash
python src/quantum_edge_core/supervisor/supervisor.py run-foreground --episode-set smoke --scenario-id S00
```

## Startup order and monitoring

- **Hub first**: the Hub (meta_agent process) handles the hot ZeroMQ -> bot plane. Always start the Hub before any bots to avoid backpressure; Supervisor/systemd should order the meta command ahead of the bot units. Pin it to dedicated P-cores (`taskset -c 0,1 ./scripts/linux/run.sh meta ...`) so it never shares CPUs with bots.
- **Bots second**: start bot services only after the Hub is healthy (`curl http://127.0.0.1:11400/health` from Supervisor or the Control Center). Bot units should use separate E-core ranges (`taskset -c 4-7`, `taskset -c 8-11`) and `nice +5` so they do not steal Hub cycles.
- **Supervised startup**: drop-in `ExecStartPost` hooks (or `After=` ordering) ensure Hub has exclusive access to P-cores before bots come online; leave Supervisor pinned to an idle core (e.g., `taskset -c 2`) so control-plane logging and approvals remain responsive.
- **Monitoring**: the future Hub heartbeat should be monitored (e.g., expected metrics emitted under `runtime/status/`); gap detection triggers alerts before bots attempt to trade. Documented pinning guidance lives in `docs/perf_tsdb.md`.

Supervisor is the control-plane, Hub is pure data-plane (no trading logic), so follow this contour rigorously.

## Systemd service

Install the unit (does not auto-start):

```bash
sudo ./deploy/systemd/install.sh
```

Enable and start:

```bash
sudo systemctl enable quantumedge-supervisor.service
sudo systemctl start quantumedge-supervisor.service
```

Status and logs:

```bash
sudo systemctl status quantumedge-supervisor.service
sudo journalctl -u quantumedge-supervisor.service -f
```

Notes:
- Customize `ExecStart` args in `deploy/systemd/quantumedge-supervisor.service` if you need a different scenario.
- For least privilege, run the service under a dedicated user and ensure `/opt/QuantumEdge` and `/etc/quantumedge` are readable.

## Systemd stack (QuestDB, Hub, Supervisor, Bots, replay)

Create `/etc/quantumedge/marketdatahub.env` for Hub-specific knobs (non-secrets). Example:

```
MARKET_DATA_ZMQ_ENDPOINT=ipc:///tmp/quantum_market_data.ipc
MARKET_DATA_SNAPSHOT_ENDPOINT=ipc:///tmp/quantum_market_snapshot.ipc
MARKET_DATA_TSDB_ENABLED=1
MARKET_DATA_L2_ENABLED=1
MARKET_DATA_STATUS_INTERVAL_SEC=10
```

Bot instances can load `/etc/quantumedge/bot@.env`:

```
# Shared bot settings
BOREAL_BOT_CONFIG=src/quantum_edge_core/ai_scalper_bot/config/bot.yaml
```

Reload systemd and enable the full stack:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now questdb marketdatahub supervisoragent quantumedge-bot@scalper quantumedge-l2-replay.timer
```

The marketdatahub service writes `runtime/status/marketdatahub.json` every 10 seconds; read it with `python -m quantum_edge_core.market_data.hub status` (or use `--json` for compact output) to verify endpoints, TSDB metrics, and L2 spool health.

Use `python tools/spool_status.py` and `python tools/prune_spool.py` (when available) to keep the spool budget in check before bots resume trading.

## Troubleshooting

- Missing venv: rerun `./scripts/linux/setup.sh`.
- Permission denied: check ownership of `/opt/QuantumEdge` and `/etc/quantumedge`, or run with sudo where needed.
- Port in use: adjust `SUPERVISOR_PORT` in `/etc/quantumedge/env`.
- Verify process: `pgrep -a -f supervisor.py` or `systemctl status quantumedge-supervisor.service`.
- Logs: `journalctl -u quantumedge-supervisor.service -f` or `logs/supervisor.log` (if configured).

## QuestDB reports

Enable TSDB in `config/tsdb.yaml` and ensure QuestDB is running. Generate a JSON report:

```bash
python src/quantum_edge_core/supervisor/supervisor.py report --last 24h --bucket 5m
```

Common queries live in `docs/tsdb_queries.md`.

Apply the schema with the helper:

```bash
python tools/questdb_apply_schema.sh --host 127.0.0.1 --port 9000
```

The script posts `deploy/questdb/schema.sql` to QuestDB's `/exec` endpoint and is safe to rerun (`CREATE TABLE IF NOT EXISTS` statements protect repeat executions).

## L2 spool reliability

- Monitor spool depth with `python tools/spool_status.py` to track total bytes, file count, and the replay cursor (`spool/l2/.replay_state.json`). If size approaches `MARKET_DATA_L2_MAX_SPOOL_GB` (default 50), clean up older files or run replay immediately.
- Schedule `tools/replay_spool.py` via cron/systemd timer to keep the backlog drained and avoid hitting the budget ceiling. Example systemd timer snippet:

```ini
[Unit]
Description=QuantumEdge L2 replay timer

[Timer]
OnCalendar=*:00/30
Persistent=true

[Install]
WantedBy=timers.target
```

Point the accompanying service at:

```bash
python tools/replay_spool.py --spool-dir runtime/spool/l2 --quest-host 127.0.0.1 --ilp-port 9009
```

- On shutdown, ensure the Hub/supervisor sequence gives `src/quantum_edge_core/market_data/spool/l2_spooler.py` time to flush (the CLI `meta_agent.py diag` can be used to confirm a clean exit). If `tools/spool_status.py` reports a large backlog, replay before starting next batch of bots.
## Smoke verification

Run `tools/smoke_e2e_stack.py` after the stack is live to assert Hot Path messages flow, QuestDB receives rows, and the L2 replay plumbing can drain the spool:

```bash
python tools/smoke_e2e_stack.py
```

The script momentarily starts QuestDB and MarketDataHub, injects synthetic events, validates snapshots, checks `market_l1`/`l2_equity` row counts, and replays the spool via `tools/replay_spool.py`.
