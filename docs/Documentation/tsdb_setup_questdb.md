# QuestDB Setup (Windows)

Quick setup for local QuestDB so SupervisorAgent can ingest telemetry.

## Install

1) Download QuestDB for Windows (zip) from the official site.
2) Extract it to a local folder, e.g. `C:\QuestDB`.

## Run

From PowerShell:

```powershell
cd C:\QuestDB
.\questdb.exe
```

Default ports:
- ILP HTTP: `http://localhost:9000/imp`
- SQL exec: `http://localhost:9000/exec`

## Configure SupervisorAgent

Edit `C:\QuantumEdge\SupervisorAgent\config\tsdb.yaml`:

```yaml
enabled: true
backend: "questdb"
questdb:
  ilp_http_url: "http://localhost:9000/imp"
  query_url: "http://localhost:9000/exec"
ingest:
  enabled: true
```

## Commands

```powershell
cd C:\QuantumEdge\SupervisorAgent
python supervisor.py tsdb-migrate
python supervisor.py tsdb-ingest start
python supervisor.py tsdb-status
```

To stop ingest:

```powershell
python supervisor.py tsdb-ingest stop
```

## Troubleshooting

- Connection refused: QuestDB is not running, or ports are blocked.
- No data in TSDB: verify `ai_scalper_bot/runtime/events/events.jsonl` and `ai_scalper_bot/runtime/status/metrics.json`.
- Ingest lag high: check `tsdb-status` and `runtime/ingest_state.json`.
