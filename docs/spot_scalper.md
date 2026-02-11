# Spot Scalper (SPOT-only Hot Path)

## Overview
The spot scalper is a deterministic hot-path loop driven by top-of-book microstructure:
`RegimeDetector -> SignalEngine -> ExecutionEngine -> RiskManager`.
It consumes MarketDataHub (`src/quantum_edge_core/market_data/`) L1 (or L2-derived L1) and emits limit order intents.
All USD-M futures endpoints are blocked for this mode.

## How to run (SPOT-only)
1) Enable the scalper and spot-only flags in `config/bot.yaml` or `src/quantum_edge_core/ai_scalper_bot/config/settings.yaml`.
2) Ensure MarketDataHub publishes `${symbol}:l1` or `${symbol}:depth_l2`.
3) Start the bot (`python src/quantum_edge_core/ai_scalper_bot/run_bot.py` or the orchestrator entrypoint `python QuantumEdge.py`).

Minimal config example:

```yaml
enabled_market: "spot"
futures_enabled: false
market_data:
  source: "hub"
  hub:
    pub_endpoint: "ipc:///tmp/quantum_market_data.ipc"
spot_scalper:
  enabled: true
  symbols: ["BTCUSDT"]
  thresholds:
    max_spread_bps: 2.0
    max_short_vol_bps: 15.0
    trend_threshold: 0.0005
    imbalance_threshold: 0.1
  fees:
    fee_bps: 5.0
    slippage_bps: 1.0
  execution:
    ttl_ms: 800
    max_requotes: 3
    tick_size: 0.01
    min_qty: 0.001
  risk:
    risk_per_trade: 0.01
    daily_dd_limit: 0.03
    max_consecutive_errors: 5
    spread_kill_bps: 10.0
    equity_usd: 0.0
```

## Required MarketDataHub topics
- `${symbol}:l1` (best bid/ask + sizes), or
- `${symbol}:depth_l2` (top-of-book derived internally).

## Decision logic (short)
- Features: `spread_bps`, `volume_imbalance`, `short_vol_bps`, `trend_score`, `exp_move_bps`.
- Regime:
  - `NO_TRADE` if `spread_bps` > `max_spread_bps`
  - `HIGH_VOL` if `short_vol_bps` > `max_short_vol_bps`
  - `TREND` if `abs(trend_score)` > `trend_threshold`
  - else `RANGE`
- Signal: imbalance-based; outputs side in {-1,0,+1} with confidence.
- Execution: limit at best bid/ask, TTL + bounded reprice, `edge_ok` if
  `exp_move_bps > spread_bps + 2*fee_bps + slippage_bps`.
- Risk: daily drawdown, spread kill, and consecutive error kill-switch.

## Known limitations
- SPOT-only; all USD-M futures endpoints are disabled.
- Minimal inventory management (no portfolio-level sizing).
- No ML training or futures execution in this loop yet.
