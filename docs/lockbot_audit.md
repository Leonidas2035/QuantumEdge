# LockBot Architecture Audit Report
> Generated: 2026-03-03 | Author: Antigravity (Lead Architect)

## 1. Migration Summary

| Item | Details |
|---|---|
| **Source** | `src/quantum_edge_core/strategies/legacy/lockbot/lockbot_btc/` |
| **Destination** | `src/quantum_edge_core/lock_bot/` |
| **Files Migrated** | 32 Python files |
| **Import Rewrites** | 42 (all `strategies.legacy.lockbot.*` → `quantum_edge_core.lock_bot.*`) |
| **Config** | `config/lockbot.yaml` (unchanged, already correct ports) |

### File Structure (post-migration)
```
src/quantum_edge_core/lock_bot/
├── __init__.py
├── config.py              # LockbotConfig dataclass + YAML loader
├── main.py                # LockBotService (764 lines) — entry point
├── contracts/
│   ├── lockbot_control_v1.py   # CommandEnvelope, AckEnvelope
│   └── lockbot_exec_v1.py      # ExecEnvelope, StatusEnvelope
├── ddn/
│   ├── config.py          # DDNConfig, DDNProfile dataclasses
│   └── engine.py          # DDNEngine (449 lines) — core math
├── execution/
│   ├── base.py            # Abstract ExecutionAdapter
│   ├── binance_futures.py # Binance Futures Testnet adapter
│   ├── ledger.py          # Trade ledger
│   └── manager.py         # ExecutionManager (order routing)
├── ipc/
│   ├── control_subscriber.py  # ZMQ SUB for Supervisor commands
│   ├── hub_subscriber.py      # ZMQ SUB for MarketDataHub ticks
│   ├── publisher.py           # ZMQ PUB for telemetry/acks
│   └── raw_subscriber.py      # Direct Binance WS fallback
├── state/
│   ├── account_state.py   # Balance/margin/position tracking
│   ├── bot_state.py       # BotState (mode, version, flags)
│   ├── market_state.py    # MarketState (VWAP, bands, volatility)
│   └── order_tracker.py   # Open order tracking
└── replay/
    ├── bot_adapter.py     # LockBotService adapter for replay
    ├── bus.py             # ReplayBus (event routing)
    ├── clock.py           # Deterministic clock for backtest
    ├── metrics.py         # Backtest metrics (PnL, Sharpe, etc.)
    ├── runner.py          # Replay runner orchestrator
    └── scenarios/
        └── generators.py  # Synthetic scenario generators
```

---

## 2. ZMQ Port Configuration

| Port | Direction | Purpose | Config Key |
|---|---|---|---|
| `tcp://127.0.0.1:5555` | **SUB** | MarketDataHub ticks | `hub_sub_endpoint` |
| `tcp://127.0.0.1:5556` | **SUB** | Supervisor policy commands | `supervisor_policy_sub_endpoint` |
| `tcp://127.0.0.1:5557` | **PUB** | Bot telemetry & acks | `bot_pub_endpoint` |

**Status**: ✅ All ports match current infrastructure (verified in `config/lockbot.yaml`).

---

## 3. Audit Questions

### 3.1 Does the bot implement VWAP and SD-Bands calculation?

**✅ YES** — Implemented in two locations:

1. **`ddn/engine.py`** → `DDNEngine._dynamic_target_from_vwap()` (L190-207)
   - Reads `ctx.market.vwap_d` (daily VWAP)
   - Adjusts DDN target delta dynamically based on price deviation from VWAP
   - Uses VWAP as the "fair value" anchor for mean-reversion logic

2. **`state/market_state.py`** → `MarketState`
   - Maintains rolling VWAP calculation with configurable `volatility_window`
   - Computes Bollinger Bands (SD-Bands) via `bb_period` and `bb_std` from config
   - Exposes `bands: Dict[str, Optional[float]]` with keys: `upper`, `lower`, `mid`

3. **`config/lockbot.yaml`** → `ddn.scalping` section:
   ```yaml
   scalping:
     enabled: true
     bb_period: 20
     bb_std: 2.0
   ```

### 3.2 Can the bot accept external mode-change commands (TREND_UNLOCK, RANGE_SCALP, PANIC_LOCK)?

**✅ YES** — Full command flow implemented:

1. **`ipc/control_subscriber.py`** → `ControlSubscriber`
   - ZMQ SUB on `tcp://127.0.0.1:5556` (Supervisor policy channel)
   - Deserializes `CommandEnvelope` via msgspec
   - Yields commands via async iterator: `async for cmd in ctrl_sub.commands()`

2. **`main.py`** → `LockBotService.process_command()` (L143-253)
   - Dispatches commands by `cmd_type`:
     - **`SET_PROFILE`** → Switches DDN profile (neutral/trend → maps to RANGE_SCALP/TREND_UNLOCK)
     - **`PANIC_LOCK`** → Forces immediate DDN intent with `action="PANIC_LOCK"`
     - **`SET_RISK_MULTIPLIER`** → Adjusts position sizing multiplier
     - **`SET_MODE`** → Switches between live/paper/halt
   - Returns `AckEnvelope` via ZMQ PUB for Supervisor confirmation

3. **`contracts/lockbot_control_v1.py`** → `CommandEnvelope`
   - Typed msgspec struct with `cmd_id`, `cmd_type`, `payload`, `timestamp`
   - Used by both Supervisor (sender) and Bot (receiver)

### 3.3 Is DDN math isolated from decision-making (DDN as pure executor)?

**✅ YES — Clean separation achieved:**

| Layer | Responsibility | File |
|---|---|---|
| **DDNEngine** (Math) | Pure function: `evaluate(ctx) → DDNDecision` | `ddn/engine.py` |
| **LockBotService** (Orchestrator) | Builds DDNContext, calls engine, routes orders | `main.py` |
| **ExecutionManager** (I/O) | Sends orders to Binance, tracks fills | `execution/manager.py` |

**DDNEngine is stateless per-call:**
- Input: `DDNContext` (intent + market snapshot + position snapshot + profile)
- Output: `DDNDecision` (verdict + order plans + reasons)
- No side effects: doesn't touch network, exchange, or state
- Only internal state: rate limiter (`_action_ts` deque) and cooldown timer

**Decision flow:**
```
Supervisor Command → LockBotService.process_command()
                     → _build_intent() → DDNIntent
                     → _build_ddn_context() → DDNContext
                     → DDNEngine.evaluate(ctx) → DDNDecision
                     → ExecutionManager.execute(order_plans)
```

The DDN Engine acts purely as a **safety-gated math layer**:
- Checks rate limits, cooldowns, liquidation risk, margin usage
- Clamps quantities to band boundaries
- Estimates transaction costs (fees + slippage)
- Returns REJECT if safety constraints violated

---

## 4. Risks & Recommendations

| Risk | Severity | Recommendation |
|---|---|---|
| `execution.api_key` in YAML | 🔴 HIGH | Move to `.env` / Secret Manager |
| No heartbeat timeout in Supervisor | 🟡 MEDIUM | Add `BOT_UNHEALTHY` detection |
| Replay framework not yet validated post-migration | 🟡 MEDIUM | Run `replay/runner.py` with synthetic scenario |
| `raw_subscriber.py` direct WS bypass | 🟢 LOW | Keep as fallback, prefer HubSubscriber |
