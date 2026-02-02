# LockBotBTC Policy Runner (Stage 4)

## Overview

The Supervisor policy runner performs regime detection and strategy selection for LockBotBTC and issues control-plane commands (`SET_REGIME`, `SET_DELTA_TARGET`, `EXEC_STEP`, `PAUSE`, `PANIC_LOCK`). The DDN layer in LockBotBTC remains the safety/execution arbiter.

## Config

File: `SupervisorAgent/configs/lockbot_btc_policy.yaml`

Key sections:

- `enabled`: turn the policy runner on/off.
- `hub_sub_endpoint` + `hub_topics`: MarketDataHub topics for BTCUSDT.
- `max_market_lag_ms`, `max_account_lag_ms`: stale-data guards.
- `execution_enabled`: when `false`, the runner never sends `EXEC_STEP`.
- `regime`: ADX/ATR/slope thresholds + hysteresis.
- `range` / `trend`: strategy parameters and band/target defaults.

## API

- `GET /api/v1/lockbot/btc/policy/status`
- `GET /api/v1/lockbot/btc/policy/decisions?limit=`
- `POST /api/v1/lockbot/btc/policy/enable` (body: `{ "enabled": true|false }`)

## CLI

From repo root:

```bash
python SupervisorAgent/supervisor.py lockbot-policy-status
python SupervisorAgent/supervisor.py lockbot-policy-enable
python SupervisorAgent/supervisor.py lockbot-policy-disable
python SupervisorAgent/supervisor.py lockbot-policy-decisions --limit 20
```

## Notes

- Regime changes are rate-limited and hysteresis-protected to prevent flip-flops.
- Range strategy uses VWAP ±2σ action zones and heatmap/funding gates.
- Trend strategy uses pullbacks to VWAP/AVWAP and wider delta bands.
- Decisions are logged to `runtime/lockbot_policy_decisions.jsonl`.
