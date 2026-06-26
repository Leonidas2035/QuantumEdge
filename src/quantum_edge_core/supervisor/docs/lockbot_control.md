# LockBotBTC Control Plane (lockbot_control.v1)

## Endpoints

- `POST /api/v1/lockbot/btc/cmd`
- `GET /api/v1/lockbot/btc/status`
- `POST /api/v1/lockbot/btc/execution/arm`
- `POST /api/v1/lockbot/btc/execution/disarm`
- `POST /api/v1/lockbot/btc/execution/cancel-all`
- `GET /api/v1/lockbot/btc/execution/status`

## Command payload (POST)

Example:

```json
{
  "cmd": "SET_REGIME",
  "regime": "RANGE",
  "reason": "ops_test"
}
```

The API wraps this into a `lockbot_control.v1` command envelope with `cmd_id`, `ts_cmd`, and `ttl_ms`.

Optional fields used by the DDN layer:

```json
{
  "cmd": "EXEC_STEP",
  "action": "ADD_LONG",
  "qty_hint": 0.02,
  "expected_edge_bps": 10.0,
  "reason": "ops_test"
}
```

Execution control examples:

```json
{
  "mode": "DEMO_TESTNET",
  "ttl_s": 300,
  "reason": "demo_window"
}
```

```json
{
  "scope": "OPEN_ONLY",
  "reason": "ops_cleanup"
}
```

## Topics

- Commands: `LOCKBOT:BTCUSDT:cmd`
- Acknowledgements: `LOCKBOT:BTCUSDT:ack`
- Status: `LOCKBOT:BTCUSDT:status`
- Exec events: `LOCKBOT:BTCUSDT:exec`

## Status payload

The status cache is updated from bot heartbeats and can be inspected via `/api/v1/lockbot/btc/status`.
DDN decisions are included under `payload.ddn.*`.
Execution status is included under `payload.execution.*`, and recent execution events are available via `/api/v1/lockbot/btc/execution/status`.

## Policy runner (Stage 4)

See `SupervisorAgent/docs/lockbot_policy.md` for regime/strategy orchestration, config, and API endpoints.
