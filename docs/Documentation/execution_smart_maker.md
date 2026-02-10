**Smart Maker Execution**
- Maker-first smart chasing for scalp entries with bounded cancel/replace and optional fallbacks (Spot + USD-M Futures).
- Post-only behavior: Spot uses `LIMIT_MAKER`; USD-M uses `LIMIT` + `GTX` (post-only).
- Safe fallbacks: `aggressive_limit` (IOC/GTC short) or `market`, configurable per policy.
- Circuit breakers: slippage, lifetime, spread/depth guards, and min remaining/notional checks.

**State Machine**
- INIT → PLACING → LIVE → REPRICING → (PARTIAL) → DONE
- CANCELING → DONE/FAILED, or ABORTED (slippage/lifetime/market quality).

**Config (bot)**
```yaml
execution:
  mode: "scalp"
  scalp:
    enabled: true
    smart_executor:
      enabled: true
      require_book: true
      market: "usdm"            # spot | usdm
      order_policy: "maker_first"   # maker_first | maker_only
      fallback_policy: "aggressive_limit"  # none | aggressive_limit | market
      max_slippage_bps: 10
      reprice_ticks: 1
      max_reprices: 3
      maker_timeout_ms: 1200
      min_reprice_interval_ms: 200
      max_lifetime_ms: 5000
      spread_max_bps: 3
      min_top_depth_usd: 2000
      min_remaining_qty: 0.001
      min_notional: 5
      aggressive_limit_offset_ticks: 1
      aggressive_limit_ttl_ms: 300
      enable_cancel_replace_throttle: true
      throttle_ms: 250
      poll_interval_ms: 50
```

**Notes**
- `maker_only` disables fallbacks; `maker_first` uses `fallback_policy` when timeout/reprices hit.
- If no book is available and `smart_executor.require_book` is true, the order is skipped (safe).
- Execution reports can be wired later via the adapter interface to reconcile fills/cancels.
