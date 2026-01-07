# Scalp ML v1 (Feature Parity + Multi-Horizon Gating)

This stage makes the Scalp Bot ML pipeline production-ready with:
- single feature builder shared by offline training + online inference
- multi-horizon models (1s / 5s / 15s)
- calibrated probabilities and per-horizon entry gating
- drift monitoring and ML snapshots for Supervisor telemetry

## Feature Parity
Source of truth:
- `ai_scalper_bot/bot/ml/features/builder.py`

Key guarantees:
- `FEATURE_SCHEMA_VERSION = "v2"`
- `feature_names()` matches runtime and training outputs
- online and offline use the same builder logic
 - microstructure features (`ofi_z`, `ofi_ma5`, `spread_bps`, `top_qty_sum`, `trade_rate_1s`, `volume_1s`) appended to the schema

## Training (offline)
Example:
```
python -m bot.ml.signal_model.train --symbol BTCUSDT --horizons 1,5,15 --data data/ticks
```

Training outputs (via ModelOps trainer):
- artifacts: `artifacts/models/<symbol>/<horizon>/<version>/`
- manifest fields include feature schema + calibration + feature stats

## Runtime Models
Runtime loader expects:
- `runtime/models/<symbol>/<horizon>/current/manifest.json`
- feature schema matches runtime builder (names + version)

If schema mismatch:
- runtime ML gating blocks entries (or disables ML based on config)

## ML Gating
Entry allowed only if all horizons meet thresholds:
- long: `p_up_h1 > T1 AND p_up_h5 > T5 AND p_up_h15 > T15`
- short: `p_down_h1 > T1 AND p_down_h5 > T5 AND p_down_h15 > T15`

Config (defaults in `config/bot.yaml`):
```
ml:
  enabled: true
  horizons: [1, 5, 15]
  thresholds:
    h1: 0.55
    h5: 0.55
    h15: 0.55
  fail_mode: "disable"   # disable | block
```

## Drift Monitoring + ML Snapshots
Bot emits `ml_snapshot` telemetry every N seconds with:
- schema_version
- model_versions (manifest hashes)
- avg p_up per horizon
- blocked entries count
- drift score + top drift features

Config:
```
ml:
  snapshot_interval_sec: 30
  drift:
    window: 300
    z_threshold: 3.0
```

## Supervisor Telemetry
ML snapshots are sent via the existing telemetry ingest endpoint:
- `POST /api/v1/telemetry/ingest`
- event type: `ml_snapshot`

Trade closes include entry probabilities when Supervisor telemetry is available:
- `POST /api/v1/telemetry/trade_result`

## Troubleshooting
- Model schema mismatch → verify manifest feature_names/version match runtime builder.
- Missing models + fail_mode=block → entries blocked until models published.
