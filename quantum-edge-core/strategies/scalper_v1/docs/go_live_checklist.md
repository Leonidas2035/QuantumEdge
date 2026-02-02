# Go-Live Checklist (Demo/Live Readiness)

## Preconditions
- Python 3.12.x venv active
- Config loaded without errors (`config/settings.yaml`)
- Models present for configured symbol/horizons
- Policy/telemetry endpoints reachable (if enabled)

## Shadow Burn-In (24h recommended)
- Run shadow mode and verify:
  - `runtime/events/events.jsonl` grows
  - `runtime/status/metrics.json` updates
  - No fatal errors or breaker loops

## Safety Rails
- Kill switch tested (on/off)
- Circuit breaker thresholds reviewed
- Data staleness thresholds reviewed
- Exit actions still allowed when entries blocked

## Demo Mode
- `app.mode=demo` set
- Demo keys set via environment (never in repo)
- Health check passes
- Orders are placed and canceled correctly in demo

## Live Enablement (if applicable)
- Verify max position and leverage limits
- Verify rate limits (orders/trades)
- Confirm telemetry + monitoring dashboards
- Confirm rollback plan and on-call contact

## Post-Go-Live
- Review metrics snapshot hourly
- Review breaker trips and rejects
- Adjust thresholds only via config updates (no code changes)
