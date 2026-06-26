# Supervisor Dashboard

The SupervisorAgent dashboard is a lightweight operator UI served by the built-in API server.

## Start

1. Start the Supervisor API:

```powershell
python supervisor.py run-foreground
```

2. Open the dashboard:

```
http://127.0.0.1:8765/dashboard
```

## Auth (optional)

Set `DASHBOARD_AUTH=token` and `DASHBOARD_TOKEN` to require a bearer token for all POST actions:

```powershell
$env:DASHBOARD_AUTH="token"
$env:DASHBOARD_TOKEN="your_token_here"
```

The dashboard will include the token in the `Authorization: Bearer ...` header for control actions.

## Key endpoints

- `GET /api/v1/dashboard/summary?symbol=BTCUSDT`
- `GET /api/v1/dashboard/events/recent?limit=200`
- `GET /api/v1/dashboard/audit/recent?limit=200`
- `GET /api/v1/dashboard/timeseries?metric=inference_p95_ms&symbol=BTCUSDT&from=...&to=...&bucket=10s`

## Controls

- Autopilot enable/disable and target state
- Policy rollout/rollback (paths are allowlisted)
- Kill switch (requires server-side challenge)

All control actions are logged to the audit trail.

Kill switch flow:

1. `GET /api/v1/safety/kill_switch` -> `{ "challenge_id": "...", "expires_at": ... }`
2. `POST /api/v1/safety/kill_switch` with `{ "enabled": true|false, "challenge_id": "..." }`
