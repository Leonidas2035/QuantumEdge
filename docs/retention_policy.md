# TSDB Retention Policy

This policy keeps SSD usage bounded while preserving useful aggregates.

## Retention targets

- L0 raw (optional): `market_trades_raw` -> 7-30 days (default: 14)
- L1 normalized: `market_l1`, `bars_1s`, `bars_1m` -> 180+ days
- L2 telemetry: `signals`, `orders`, `fills`, `positions`, `equity`, `risk_events` -> 180+ days

Retention days are defined in `config/tsdb.yaml` under `retention_days`.

## Operational procedure (manual)

1) Export older partitions to Parquet.
2) Verify exports exist and are readable.
3) Run purge in dry-run mode to verify the SQL.
4) Run purge with `--apply`.

## Purge workflow

Dry run (recommended first):

```bash
python tools/tsdb/purge_partitions.py --dry-run
```

Apply retention:

```bash
python tools/tsdb/purge_partitions.py --apply
```

## Export workflow

Export a daily range for a table:

```bash
python tools/tsdb/export_parquet.py --table bars_1s --symbol BTCUSDT --from 2025-01-01 --to 2025-01-07
```

Outputs land in `archive/parquet/<table>/<symbol>/YYYY-MM-DD/` by default.

Note: `export_parquet.py` requires `pyarrow` to write Parquet files.
