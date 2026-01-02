# Upgrade / Migration Guide

## Version update

1) Pull the latest changes.
2) Update the package:

```
pip install -e .
```

3) Verify:

```
meta-agent version
meta-agent diag
```

## Runtime changes

New stages may add fields under:
- `runtime/runs/<run_id>/report.json`
- `runtime/runs/<run_id>/events.jsonl`
- `runtime/scheduler/state.json`

These changes are additive; older runs remain readable.

## Scheduler state migration

- The scheduler reads `runtime/scheduler/state.json`.
- If the file is missing, it is recreated automatically.
- If fields are missing, defaults are applied.

## Schedule files

- Schedules are read from `runtime/schedules/` first, then `schedules/`.
- Ensure schedule YAMLs match the schema in `docs/scheduler.md`.

## Common issues after upgrade

- **lock_busy (50)**: an old process still holds the lock. Stop it or remove the lock file.
- **UI 403**: refresh the UI to get a new token.
- **invalid_task (40)**: update task YAML to match current TaskSpec fields.
