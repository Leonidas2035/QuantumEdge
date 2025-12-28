# Stage 2 ML Pipeline (Features + Labels + Datasets + Baseline Training)

This stage converts S00–S24 scenario episodes into leak-safe ML datasets, then trains baseline
multi-horizon models (h=1,5,30) with manifests compatible with `QuantumEdge.py diag`.

## Feature Schema

Print the current feature schema contract:

```bash
python -m bot.ml.features.diag_schema
```

## Build Datasets from Scenarios

Prerequisite: Stage-1 scenario outputs exist under `data/scenarios/<SYMBOL>`.

```bash
python -m bot.ml.datasets.build_from_scenarios \
  --symbol BTCUSDT \
  --scenarios-root data/scenarios/BTCUSDT \
  --out data/ml/BTCUSDT \
  --horizons 1 5 30 \
  --mode seconds \
  --label-thr-bps 2.0
```

Outputs:

```
data/ml/BTCUSDT/
  config_snapshot.json
  schema.json
  horizon_h1/{train,val,test}.csv
  horizon_h5/{train,val,test}.csv
  horizon_h30/{train,val,test}.csv
  reports/dataset_report.md
```

## Validate Datasets

```bash
python -m bot.ml.datasets.validate --root data/ml/BTCUSDT
```

## Train Baseline Models

```bash
python -m bot.ml.signal_model.train \
  --symbol BTCUSDT \
  --data-root data/ml/BTCUSDT \
  --horizons 1 5 30 \
  --publish-runtime
```

Artifacts:

```
artifacts/models/BTCUSDT/h1/model.json
artifacts/models/BTCUSDT/h1/manifest.json
```

If `--publish-runtime` is set and `QuantumEdge.py` is found, models are copied to:

```
runtime/models/BTCUSDT/1/current/manifest.json
```

## Labels (Leak-Safe)

Labels use a strictly future horizon shift on 1s bars:

```
fut_ret_h = (price[t+h] / price[t]) - 1
y_up_h = fut_ret_h > max(label_thr_bps, fee_bps+slippage_bps)
```

An optional ignore zone drops near-zero returns.

## Failure Modes

- Missing `splits/split_time.json`: run Stage-1 scenario build first.
- Too few rows after warmup: increase episode length or reduce horizon.
- Schema mismatch: update datasets to match `feature_schema.py`/`features/builder.py`.
