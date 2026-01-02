# Meta-Agent Operations

## Commands

- `python meta_agent.py diag`
- `python meta_agent.py health`
- `python meta_agent.py run-task --task examples/tasks/001_refactor_small.yaml`
- `python meta_agent.py status --limit 5`
- `python meta_agent.py watch --inbox runtime/inbox --poll-seconds 2 --archive runtime/inbox_done --failed runtime/inbox_failed`
- `python meta_agent.py ui --port 8766 --bind 127.0.0.1`
- `python meta_agent.py run-scheduler --once`
- `python meta_agent.py scheduler-status`

## Runtime layout

```
runtime/
  runs/<run_id>/
    task.yaml
    report.json
    patches/
    gates/
    shadow/
    context_manifest.json
  logs/meta_agent.log
  scheduler/state.json
  schedules/
  inbox/
  inbox_done/
  inbox_failed/
```

## Quality Gates & Shadow

When gates are configured, Meta-Agent applies changes in a shadow workspace and runs gate steps
before applying to the real project. If gates fail or time out, no real apply occurs and the run
returns `exit_code=12`.

Dry-run (`execution.dry_run: true`) never applies to the real project. It can still run gates in
shadow and returns `exit_code=11`.

Gate outputs are saved under `runtime/runs/<run_id>/gates/*.out` and `*.err`.

Example snippet:
```yaml
execution:
  dry_run: true
gates:
  enabled: true
  steps:
    - name: smoke
      cmd: ["python", "-c", "import sys; sys.exit(0)"]
```

## Off-market Scheduler

Schedules are YAML files stored in `runtime/schedules/` (or `schedules/` in the repo). Each
schedule defines maintenance windows (Europe/Kyiv by default), trigger cadence, retry policy,
and a TaskSpec template.

Key files:
- Schedules: `runtime/schedules/*.yaml`
- State: `runtime/scheduler/state.json`

Example schedule:
- `examples/schedules/001_nightly_maintenance.yaml`

Run once:
```
python meta_agent.py run-scheduler --once
```

Continuous loop:
```
python meta_agent.py run-scheduler --tick-seconds 2
```

Status:
```
python meta_agent.py scheduler-status
```

STOP/PAUSE:
- Create `STOP` in the inbox to exit scheduler gracefully.
- Create `PAUSE` to pause enqueue/processing while keeping state updates.

Retries/backoff:
- Transient errors (exit_code 30, 50) back off exponentially with optional jitter.
- Non-transient errors (invalid_task, block, gate_failed) do not retry.

## Projects registry

Projects for the Control Center are defined in `config/projects.yaml`.
Add a new entry under `projects` with `id`, `root`, `label`, and optional
`default_include_globs`/`deny_globs`.

## Service usage

Linux (systemd): see `ops/systemd/meta-agent-watch.service`.

Windows (NSSM): see `ops/windows/nssm_install.ps1`.

## Exit codes (run-task)

- `0` allow + applied
- `10` warn (patch/report only)
- `11` dry_run_complete (patch/report only)
- `12` gate_failed (patch/report only)
- `20` block (patch/report only)
- `30` error
- `40` invalid_task
- `50` lock_busy

## Troubleshooting

- `lock busy`: another Meta-Agent process is running; wait or stop it.
- `invalid_task`: check required fields in `task.yaml`.
- Logs: `runtime/logs/meta_agent.log`
