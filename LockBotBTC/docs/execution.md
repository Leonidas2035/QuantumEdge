**LockBotBTC Execution Layer (Stage 6)**
- Purpose: safely submit DDN order plans to Binance USD-M Futures with explicit arming and full auditability.
- Default mode: `DRY_RUN` (no exchange calls).

**Execution Modes**
- `DRY_RUN`: no external calls; emits `EXECUTION_DISABLED` events.
- `DEMO_TESTNET`: uses Binance Futures testnet/demo endpoints.
- `LIVE_MAINNET`: available but disabled unless `execution.allow_live_mainnet=true`.

**Arming Workflow**
- Supervisor must send `ARM_EXECUTION` with `mode` + `ttl_s`.
- Bot starts disarmed and auto-disarms on:
  - TTL expiry
  - account stream stale (see `execution.stale_account_ms`)
  - repeated API errors (see `execution.error_threshold`)

**Safety Rules**
- Idempotent client order IDs: `LBTC_<cmd_id>_<index>`.
- Reduce-only enforcement for trims; add actions cannot be reduce-only.
- Open order cap: `execution.max_open_orders`.
- In `PANIC` mode, add-risk steps are blocked unless `execution.allow_reduce_only_in_panic=false`.

**Account Streams (for reconciliation)**
- Configure `account_topics` in `LockBotBTC/config/lockbot_btc.yaml` to include:
  - `account:snapshot`
  - `account:delta:usdm_ws`
  - `account:delta:usdm` (alias for backward compatibility)
  - Note: MarketDataHub publishes account deltas as `account:delta:{delta.src}` (usdm -> `account:delta:usdm_ws`).

**Exec Events**
- Topic: `LOCKBOT:BTCUSDT:exec` (schema `lockbot_exec.v1`)
- Types: `ORDER_SUBMITTED`, `ORDER_ACKED`, `ORDER_REJECTED`, `ORDER_PARTIALLY_FILLED`,
  `ORDER_FILLED`, `ORDER_CANCELED`, `RECONCILIATION_MISMATCH`, `EXECUTION_DISABLED`

**Config Knobs**
- `execution.mode`
- `execution.auto_submit_on_allow`
- `execution.allow_live_mainnet`
- `execution.ledger_path`
- `execution.base_url` (testnet/demo)
