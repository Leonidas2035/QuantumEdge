# Off-Market Scheduler

The scheduler creates tasks during maintenance windows (Europe/Kyiv by default),
enqueues them in `runtime/inbox`, and updates `runtime/scheduler/state.json`.
Schedules are read from `runtime/schedules/` first, then `schedules/` if present.

## Schedule schema (YAML)

```yaml
schedule_id: nightly_maintenance
enabled: true
timezone: Europe/Kyiv
project_id: meta_agent
inbox_dir: runtime/inbox
archive_dir: runtime/inbox_done
failed_dir: runtime/inbox_failed
windows:
  - days: ["*"]   # mon..sun or "*"
    start: "02:00"
    end: "05:00"
trigger:
  type: interval
  every_seconds: 900
task_template:
  objective: "Nightly docs tidy"
  instructions: "Update docs formatting"
  execution:
    dry_run: true
  mode: task
policy:
  max_concurrent: 1
  max_runs_per_window: 10
  max_attempts: 3
retries:
  enabled: true
  backoff_base_seconds: 15
  backoff_max_seconds: 300
  jitter: true
```

Example file: `examples/schedules/001_nightly_maintenance.yaml`.

## Windows and timezones

- Timezone defaults to `Europe/Kyiv`.
- Window start/end use `HH:MM` in local time.
- Windows that cross midnight are supported (e.g. 22:00–02:00).

## Triggers

- `interval`: fire every `every_seconds`.
- `cron` (minimal): supports `*`, `*/N`, or exact integers for `minute` and `hour`.
- `once`: runs a single time, then no further fires.

## Retries and backoff

Transient exit codes:
- `30` error
- `50` lock_busy

Non-transient exit codes:
- `20` block
- `12` gate_failed
- `11` dry_run_complete
- `40` invalid_task

Backoff is exponential: `base * 2^attempt`, capped at `backoff_max_seconds`.
With jitter enabled, delay is randomized ±20%.

## Crash recovery

The scheduler checks `state.json` and the inbox:
- If a previously enqueued task is still in inbox or already archived/failed, it is not duplicated.
- If the file is missing, the scheduler marks `last_task_missing` and continues.

## State file

`runtime/scheduler/state.json` (atomic write) tracks:
```
{
  "last_tick": "...",
  "schedules": {
    "<id>": {
      "last_fire": "...",
      "last_task_file": "...",
      "last_run_id": "...",
      "last_exit_code": 0,
      "attempts": 0,
      "next_eligible_at": "...",
      "window_runs": { "YYYYMMDD": 1 }
    }
  }
}
```
