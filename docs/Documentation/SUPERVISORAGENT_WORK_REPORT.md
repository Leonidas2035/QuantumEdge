# SupervisorAgent Work Report

## 1) Overview (Hub -> Bots -> SupervisorAgent)

SupervisorAgent is the control-plane for the contour:
- Hub is the data-plane (ZMQ PUB/SUB, TSDB).
- Bots are execution-plane (consume Hub directly).
- SupervisorAgent orchestrates processes, health, and unified APIs.

SupervisorAgent does not forward market/account payloads.

## 2) Architecture Modules

- `config/processes.yaml`: process specs (cwd/cmd/env/health/restart).
- `supervisor/process_spec.py`: dataclasses for specs and status.
- `supervisor/config_loader.py`: YAML loader + validation.
- `supervisor/process_manager.py`: multi-process manager with restart policy and health checks.
- `supervisor/api_server.py`: control-plane endpoints and events tail.
- `supervisor/events.py`: telemetry ledger (schema v1, run_id/trace_id, retention).

## 3) How to Run

```bash
python SupervisorAgent/supervisor.py run
```

Config files:
- `SupervisorAgent/config/supervisor.yaml`
- `SupervisorAgent/config/processes.yaml`

## 4) New Endpoints

- `GET /api/v1/system/status`
- `POST /api/v1/process/<name>/start`
- `POST /api/v1/process/<name>/stop`
- `POST /api/v1/process/<name>/restart`
- `GET /api/v1/events/tail`

## 5) Known Limitations / TODO

- ZMQ stream subscriber for lag/silence metrics is not implemented in this PR.
- Health checks are basic (HTTP/TCP) with short timeouts.

## 6) Checklist (Tests)

Run:

```bash
python -m py_compile SupervisorAgent/supervisor.py
pytest -q
```
