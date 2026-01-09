# LockBotBTC Control Plane (lockbot_control.v1)

## Endpoints

- `POST /api/v1/lockbot/btc/cmd`
- `GET /api/v1/lockbot/btc/status`

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

## Topics

- Commands: `LOCKBOT:BTCUSDT:cmd`
- Acknowledgements: `LOCKBOT:BTCUSDT:ack`
- Status: `LOCKBOT:BTCUSDT:status`

## Status payload

The status cache is updated from bot heartbeats and can be inspected via `/api/v1/lockbot/btc/status`.

