# Ops Runbook (QuantumEdge Scalp Bot)

## Start/Stop
- Start (shadow mode): `python -m bot.ops.smoke --mode shadow --minutes 2`
- Start (normal run): `python bot/run_bot.py`
- Stop: Ctrl+C (graceful)

## Modes
- Shadow: full pipeline runs, no orders placed.
- Demo: `app.mode=demo` with demo credentials (no real funds).

## Safety Rails
- Kill switch: `risk.kill_switch.enabled` or `state/kill_switch.json`.
- Circuit breakers: configured under `risk.circuit_breakers`.
- Data staleness: `data.max_tick_staleness_ms` and `data.max_book_staleness_ms`.
- Exits are allowed even when entries are blocked.

## Observability
- Local event stream: `runtime/events/events.jsonl`
- Metrics snapshot: `runtime/status/metrics.json`
- Bot status: `state/bot_status.json`

## Reason Codes
- `KILL_SWITCH_ACTIVE`, `DATA_STALE`
- `CIRCUIT_BREAKER_ACTIVE:<type>`
- `RISK_LIMIT_*`, `RATE_LIMIT_*`
- `ML_THRESHOLD_FAIL_*`, `SCHEMA_HASH_MISMATCH`

## Breaker Recovery
1) Identify reason in `metrics.json`.
2) Fix root cause (data feed, latency, error bursts).
3) Wait for cooldown or restart process.

## Logs
- `logs/` for runtime logs (if enabled).
- `runtime/events/` for JSONL event stream.

## Common Failures
- Models missing or schema mismatch → entries blocked.
- Stale data → entries blocked.
- Excessive errors → circuit breaker trips.
