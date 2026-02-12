# LockBotBTC DDN Layer (Stage 3)

## What it does

- Enforces delta bands and clamps requested steps to stay within the active band.
- Applies safety guards: stale data, margin usage cap, liquidation distance, rate limit, cooldown.
- Estimates execution cost (fees + slippage + funding) and blocks negative edge steps.
- Emits deterministic decisions (`ALLOW`, `MODIFY`, `REJECT`, `PANIC_ONLY`) plus an order-plan preview.

## What it does not do

- No strategy selection (no RANGE/TREND logic).
- No execution to exchange (order plans are only recommendations).
- No VWAP entry logic (Stage 4+).

## Rule precedence (first match wins)

1. Stale data guard (only PANIC/PAUSE allowed).
2. Liquidation distance guard or `PANIC_LOCK` intent → panic hedge plan.
3. PAUSE/RESUME passthrough.
4. Rate limit and cooldown.
5. Margin usage cap (blocks add-risk actions).
6. Profile/target updates (`SET_REGIME`, `SET_DELTA_TARGET`).
7. Delta band clamp + min step notional.
8. Cost guard (fee + slippage + funding).

## Config knobs (LockBotBTC/config/lockbot_btc.yaml)

- `ddn.profiles.*` (target + band_low/band_high)
- `ddn.max_band_abs`
- `ddn.max_margin_usage`
- `ddn.min_distance_to_liq_bps`
- `ddn.max_step_notional_usd`, `ddn.min_step_notional_usd`
- `ddn.max_steps_per_minute`, `ddn.cooldown_ms_after_reject`
- `ddn.panic_on_lag_ms`
- `ddn.taker_fee_bps`, `ddn.maker_fee_bps`, `ddn.expected_slippage_bps_market`
- `ddn.funding_weight`, `ddn.min_expected_edge_bps`, `ddn.max_cost_bps_per_step`

## Supervisor usage (intents)

- `SET_REGIME` selects the DDN profile (`neutral`, `trend`, `panic`).
- `SET_DELTA_TARGET` sets target + band; values are clamped to `max_band_abs`.
- `EXEC_STEP` can include `expected_edge_bps` to enable the cost guard.
- `PANIC_LOCK` forces an immediate hedge plan (reduce-only, market).

## Status output

DDN decisions are surfaced in the status payload:

- `payload.ddn.last_verdict`
- `payload.ddn.last_reasons`
- `payload.ddn.last_step_qty`
- `payload.ddn.last_cost_bps`
- `payload.ddn.order_plans`
