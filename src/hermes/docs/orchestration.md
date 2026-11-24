# Supervisor Orchestration (Control-Plane)

SupervisorAgent is the control-plane for Hub and Bots. It manages processes,
runs health checks, and exposes control APIs. It does NOT forward market or
account data.

## Processes config

File: `SupervisorAgent/config/processes.yaml`

Example:

```yaml
version: 1
defaults:
  env:
    PYTHONUNBUFFERED: "1"
  restart:
    enabled: true
    max_retries: 5
    backoff_s: [1, 2, 4, 8, 16]
    cooldown_s: 30
processes:
  hub:
    enabled: true
    cwd: "../MarketDataHub"
    cmd: ["python", "-m", "MarketDataHub.hub"]
    healthcheck:
      type: "http"
      url: "http://127.0.0.1:8700/health"
      timeout_s: 2
  bot_qe_1:
    enabled: true
    cwd: ".."
    cmd: ["python", "QuantumEdge.py", "run", "--profile", "qe1"]
    healthcheck:
      type: "tcp"
      host: "127.0.0.1"
      port: 8765
      timeout_s: 1
```

Notes:
- `cwd` is resolved relative to repo root if not absolute.
- `cmd` must be a list of strings.
- `healthcheck.type` must be `none`, `http`, or `tcp`.
- Legacy `/api/v1/bot/*` endpoints map to the first process named `bot` or starting with `bot`.

## Supervisor loop

The supervisor loop:
1) Ensures enabled processes are running (idempotent start).
2) Runs health checks with short timeouts.
3) Applies restart/backoff policy on crashes.

## Control-plane API

Requires `X-API-TOKEN` if configured in `config/supervisor.yaml`.

Endpoints:
- `GET /api/v1/system/status`
- `POST /api/v1/process/<name>/start`
- `POST /api/v1/process/<name>/stop`
- `POST /api/v1/process/<name>/restart`

Windows/Linux examples:

```powershell
# Windows PowerShell
Invoke-RestMethod -Method Get -Headers @{ "X-API-TOKEN" = "<token>" } http://127.0.0.1:8765/api/v1/system/status
```

```bash
# Linux/macOS
curl -H "X-API-TOKEN: <token>" http://127.0.0.1:8765/api/v1/system/status
```
