# Zero Trades Diagnostic Report
> Session: 5-hour Testnet run | Date: 2026-03-04 | Author: Antigravity

## Executive Summary

LockBot detected `IMBALANCED` delta at startup but **executed zero trades** during a 5-hour session. The root cause is **architectural, not parametric**: the DDN engine is a pure safety gate that only evaluates externally-initiated commands — it has no autonomous trading loop. Combined with a crashed Supervisor (no ZMQ directives ever sent) and a stale-data guard that blocks all actions when market feeds are `None`, the engine never reached the order-planning stage.

---

## 1. Supervisor Log Analysis (`logs/supervisor.log`)

**27 lines total — all errors.** The Supervisor **crashed on import**:

```
ModuleNotFoundError: No module named 'aiohttp'
```

**Impact**: The Supervisor never started → never called `run_check()` → never published `directive.v1` on ZMQ port 5556 → LockBot's `_directive_loop()` received **zero messages**.

---

## 2. LockBot Log Analysis (`logs/lockbot.log`)

**File does not exist.** The bot either:
- Never started as an independent process, or
- Its `structlog` output went to stdout only (no file handler configured for `logs/lockbot.log`)

The YAML sets `log_path: "logs/lockbot.log"` but `LockBotService.__init__` doesn't open this file — logging is configured only in `_run()` via `structlog.configure()` with `ConsoleRenderer`.

---

## 3. DDN Engine Analysis — Why Zero Orders

### 3.1 Critical Blocker: No Autonomous Trading Loop

The DDN engine's `evaluate()` ([engine.py:L94](file:///home/l_garnatko/QuantumEdge/src/quantum_edge_core/lock_bot/ddn/engine.py#L94)) is **never called on its own**. The call chain is:

```
ZMQ command arrives → _cmd_loop() → process_command() → _build_intent() → DDNEngine.evaluate()
```

There is **no tick-driven loop** that periodically checks "should I trade now?" The bot sits idle unless a `CommandEnvelope` arrives on ZMQ. Without the Supervisor (crashed), no commands ever arrive.

### 3.2 STALE_DATA Guard (Line 127-133)

```python
if _is_stale(ctx.market.market_lag_ms, ctx.position.account_lag_ms, ...):
    return self._reject(now_ms, ["STALE_DATA"])
```

`_is_stale()` returns `True` when **either lag is `None`** ([engine.py:L471](file:///home/l_garnatko/QuantumEdge/src/quantum_edge_core/lock_bot/ddn/engine.py#L471)):

```python
def _is_stale(market_lag_ms, account_lag_ms, limit_ms):
    if market_lag_ms is None or account_lag_ms is None:
        return True  # ← ALWAYS True at startup
```

At startup, `MarketState.last_market_ts = None` and `AccountState.last_account_ts = None`. Until the first tick arrives on MarketDataHub (port 5555) **and** the first account update arrives, every `evaluate()` call returns `STALE_DATA`.

### 3.3 NEGATIVE_EDGE Guard (Line 203-205)

```python
if not self._cost_guard(intent.expected_edge_bps, cost_bps):
    return self._reject(now_ms, ["NEGATIVE_EDGE"])
```

When `expected_edge_bps` is `None` (not provided by Supervisor), the guard falls back to:
```python
return cost_bps <= self._cfg.max_cost_bps_per_step  # 20 bps
```

With `taker_fee_bps=4.0` + `slippage=5.0` = **9 bps** per trade — this passes. But if the Supervisor sent a command with `expected_edge_bps=0`, it would be rejected because `0 < 1.0 + 9.0`.

### 3.4 MIN_STEP Guard (Line 197-201)

```python
if adjusted_qty * mark_price < self._cfg.min_step_notional_usd:
    return self._reject(now_ms, ["MIN_STEP"])
```

YAML config sets `min_step_notional_usd: 100.0`. At BTC ~$97,000, minimum order = `100/97000 = 0.00103 BTC`. This is reasonable, but on a small testnet balance ($1000), each $100 step = 10% of capital — only ~5 steps before margin cap.

### 3.5 BAND_CLAMP Guard (Line 194-196)

The `neutral` profile has bands `[-0.05, 0.05]` BTC. If the bot's net_delta is already near the band boundary, `_clamp_to_band()` returns `None` → `BAND_CLAMP` reject.

---

## 4. Config Analysis (`config/lockbot.yaml`)

| Parameter | Value | Issue |
|---|---|---|
| `min_step_notional_usd` | `100.0` | OK for $1K+ balance, but leaves only ~5 steps |
| `cooldown_ms_after_reject` | `1000` | 1 second cooldown after any reject — cascading rejects lock out the bot |
| `panic_on_lag_ms` | `5000` | 5s stale data limit — may be too tight for testnet latency |
| `neutral.band_low/high` | `±0.05` | Very tight — allows only 0.05 BTC delta deviation |
| `max_margin_usage` | `0.5` | 50% margin cap — reasonable |
| `max_velocity_bps_per_sec` | `50.0` | May trigger on normal 5s testnet tick gaps |

---

## 5. What's Missing — Required Capabilities

### 5.1 Autonomous Tick-Driven Trading Loop
DDN needs a `_trading_loop()` that runs on every market tick:
```
on_tick(mark_price) → assess delta deviation from target → if deviation > threshold → generate intent → evaluate() → submit order
```
Currently, this loop **does not exist**. The engine only reacts to external ZMQ commands.

### 5.2 Price-Level Triggers (Entry Signals)
DDN has no concept of "buy at price X". It only knows:
- Current delta vs target delta
- Band boundaries

Missing indicators for active trading:
- **Micro-VWAP deviation trigger**: "price is 0.3% below VWAP → ADD_LONG"
- **Band-touch trigger**: "price hit lower Bollinger Band → mean-reversion entry"
- **Funding rate arbitrage**: "funding negative → go long, collect funding"

### 5.3 DCA Grid Logic
No grid/ladder order system. DDN places single orders per command. Needed:
- Spread N limit orders across a price range
- Auto-replace filled orders at new levels

---

## 6. Root Cause Summary (Priority Order)

| # | Cause | Severity | File:Line |
|---|---|---|---|
| 1 | **No autonomous trading loop** — bot only reacts to ZMQ commands | 🔴 CRITICAL | [main.py](file:///home/l_garnatko/QuantumEdge/src/quantum_edge_core/lock_bot/main.py) — missing `_trading_loop()` |
| 2 | **Supervisor crashed** — `aiohttp` import error, zero directives sent | 🔴 CRITICAL | [supervisor.log](file:///home/l_garnatko/QuantumEdge/logs/supervisor.log) |
| 3 | **STALE_DATA** guard rejects when lag timestamps are `None` | 🟡 HIGH | [engine.py:L127-133](file:///home/l_garnatko/QuantumEdge/src/quantum_edge_core/lock_bot/ddn/engine.py#L127-L133) |
| 4 | **No entry signal logic** — DDN doesn't know when to initiate trades | 🟡 HIGH | [engine.py](file:///home/l_garnatko/QuantumEdge/src/quantum_edge_core/lock_bot/ddn/engine.py) — missing price-level triggers |
| 5 | **Cooldown cascade** — 1s cooldown after STALE_DATA blocks subsequent attempts | 🟢 MEDIUM | [engine.py:L160-170](file:///home/l_garnatko/QuantumEdge/src/quantum_edge_core/lock_bot/ddn/engine.py#L160-L170) |
