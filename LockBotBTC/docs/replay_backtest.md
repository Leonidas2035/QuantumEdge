**LockBotBTC Replay/Backtest Harness**
- Purpose: deterministic validation of Supervisor policy (Stage 4) + LockBotBTC DDN (Stage 3) without live execution.
- Inputs: MarketDataHub contract topics in JSONL format (lockbot_md.v1) plus optional replay-only position snapshots.
- Outputs: `decisions.jsonl`, `metrics.json`, `summary.md`, `run_metadata.json` in the chosen output directory.

**Run Synthetic Scenarios**
- Command:
  ```bash
  python LockBotBTC/tools/lockbot_replay.py run --scenario S_RANGE_OSCILLATION --duration-s 1800 --out runtime/replay_runs/range_1800
  ```
- Available scenarios:
  - `S_RANGE_OSCILLATION`
  - `S_TREND_UP_PULLBACKS`
  - `S_TREND_DOWN_PULLBACKS`
  - `S_TREND_FLIP_FALSE_BREAK`
  - `S_VOLATILITY_EXPANSION_ATR_SPIKE`
  - `S_FUNDING_BLEED_LONG_DURATION`

**Run Recorded Dataset**
- Format: JSONL where each line matches the lockbot market-data envelope:
  ```json
  {"schema":"lockbot_md.v1","topic":"BTCUSDT:mark_price_1s","symbol":"BTCUSDT","ts_event":1730000000000,"ts_pub":1730000000000,"source":"replay","seq":1,"payload":{"mark_price":40000.0}}
  ```
- Optional replay-only account event:
  ```json
  {"schema":"lockbot_account.v1","topic":"BTCUSDT:position_snapshot","symbol":"BTCUSDT","ts_event":1730000000000,"ts_pub":1730000000000,"source":"replay","seq":1,"payload":{"positions":{"long_qty":0.2,"short_qty":0.2},"risk":{"margin_usage":0.3,"distance_to_liq_bps":600.0}}}
  ```
- Command:
  ```bash
  python LockBotBTC/tools/lockbot_replay.py run --dataset data/replay.jsonl --out runtime/replay_runs/dataset_run --time-min 1730000000000 --time-max 1730003600000
  ```

**Key Options**
- `--config` policy config: `SupervisorAgent/configs/lockbot_btc_policy.yaml`
- `--bot-config` bot config: `LockBotBTC/config/lockbot_btc.yaml`
- `--ddn-config` optional DDN overrides (same schema as `ddn` block in bot config)
- `--paper-fill-model` `tierA` (delta-only MTM) or `tierB` (simple fills)
- `--execution-enabled` allows `EXEC_STEP` intents during replay (default off in policy config)

**Artifacts**
- `decisions.jsonl`: policy decisions + cmd/ack/status trace
- `metrics.json`: safety + activity + paper-PnL estimates
- `summary.md`: human-readable summary
- `run_metadata.json`: configs + git SHA for reproducibility

**Caveats**
- Paper PnL is an estimate; no live execution or exchange fees.
- Synthetic scenarios are deterministic fixtures, not a market-accurate simulator.
- Account snapshots are replay-only unless connected to real account streams.
