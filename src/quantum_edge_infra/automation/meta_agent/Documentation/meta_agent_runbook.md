# Meta-Agent Runbook (multi-project + off-market)

- Configure projects in `config/projects.yaml` (default ai_scalper_bot, supervisor_agent, meta_agent).
- GUI: select project, enter task name & body, click Add; stage saved with project field.
- CLI helpers:
  - `python meta_agent.py --list-projects`
  - `python meta_agent.py --project-id supervisor_agent` (override project for stage run)

## Off-Market / Supervisor Maintenance
- Schedule config: `config/offmarket_schedule.yaml` (UTC window, day allow list, max_runs_per_day, require_bot_idle, backlog limits).
- State file: `state/offmarket_state.json` (last_run_utc, runs_today, last_run_result).
- Runner: `offmarket_scheduler.py` (one-shot) decides if maintenance should run, then calls `supervisor_runner.run_supervisor_maintenance_once`.
- Entry: `python offmarket_runner.py` (or call `offmarket_scheduler.main` from cron/Task Scheduler).
- Supervisor reports directory: `reports/supervisor/` (used to build backlog). Logs: `logs/offmarket_scheduler.log`.

## TSDB Infrastructure (QuestDB / ClickHouse / Timescale)
- Docker profiles live in `infra/tsdb/docker-compose.tsdb.yml` with QuestDB, ClickHouse, and Timescale options.
- Copy `infra/tsdb/.env.example` to `.env` and adjust credentials.
- Start a backend:
  - ClickHouse: `docker compose -f infra/tsdb/docker-compose.tsdb.yml --profile clickhouse up -d`
  - QuestDB: `docker compose -f infra/tsdb/docker-compose.tsdb.yml --profile questdb up -d`
  - Timescale: `docker compose -f infra/tsdb/docker-compose.tsdb.yml --profile timescale up -d`
- Stop: `docker compose -f infra/tsdb/docker-compose.tsdb.yml --profile <name> down`
- Wipe data (danger): append `-v` to `down`.
- SupervisorAgent `config/tsdb.yaml` should point to the selected backend (e.g., ClickHouse http://localhost:8123, DB quantumedge, user/password from .env).

## Ops deployment (Windows & Linux)
- Scripts live in `infra/ops/`.
- **Windows (NSSM)**: copy `windows.env.example` to `windows.env`, set paths (Python, repos, entrypoints), then run:
  - `infra/ops/windows/install_services.ps1` to install `QuantumEdgeBot` and `SupervisorAgent` services.
  - `start_services.ps1` / `stop_services.ps1` to control them, `uninstall_services.ps1` to remove.
  - Requires `nssm.exe` (see `infra/ops/windows/nssm/README.md`).
- **Linux (systemd)**: update unit templates in `infra/ops/linux/`, then `sudo ./install_systemd.sh` to place units and env files under `/etc/quantumedge/`. Uninstall with `sudo ./uninstall_systemd.sh`.
- Health helpers: `scripts/check_health.ps1` or `scripts/check_health.sh` call SupervisorAgent `/api/v1/dashboard/health`.
