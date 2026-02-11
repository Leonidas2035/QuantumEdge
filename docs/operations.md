# Meta-Agent Operations Runbook

This runbook covers core commands, runtime locations, and troubleshooting steps.

## Commands

### Diagnostics and health

```
python meta_agent.py diag
python meta_agent.py health
python meta_agent.py status --limit 5
python meta_agent.py version
```

### Run a task

```
python meta_agent.py run-task --task examples/tasks/001_refactor_small.yaml
```

Common options:
- `--json` machine-readable summary output
- `--timeout-seconds <N>` overall task timeout
- `--llm-timeout-seconds <N>` LLM request timeout
- `--retries <N>` retry transient errors
- `--runtime-dir <path>` override runtime root

### Watch inbox

```
python meta_agent.py watch --inbox runtime/inbox --poll-seconds 2 --archive runtime/inbox_done --failed runtime/inbox_failed
```

Controls:
- `STOP` file in inbox -> graceful exit
- `PAUSE` file in inbox -> pause enqueue/processing

### Scheduler

```
python meta_agent.py run-scheduler --once
python meta_agent.py run-scheduler --tick-seconds 2
python meta_agent.py scheduler-status
```

### Control Center UI

```
python meta_agent.py ui --port 8766 --bind 127.0.0.1
```

### Approve & apply (CLI)

```
python meta_agent.py approve-apply --run-id <run_id>
```

Approve/apply is allowed only for `warn` verdicts and always re-runs gates in shadow.

### Dump run summary

```
python meta_agent.py dump-run --run-id <run_id>
python meta_agent.py dump-run --run-id <run_id> --json
```

## Runtime layout

```
runtime/
  runs/<run_id>/
    task.yaml
    report.json
    patches/
    gates/
    shadow/
    changeset.json
    context_manifest.json
    approval/
    events.jsonl
  logs/
    meta_agent.log
    control_center.log
  scheduler/state.json
  schedules/
  inbox/
  inbox_done/
  inbox_failed/
```

## Quality gates & shadow

- Gate steps run in shadow workspaces before applying to the real repo.
- Gate output logs live in `runtime/runs/<run_id>/gates/`.
- Dry-run (`execution.dry_run: true`) never applies and returns exit_code `11`.
- Gate failure returns exit_code `12` and never applies.

## Projects registry

Projects for the Control Center are defined in `src/quantum_edge_core/config/projects.yaml`.
Add a new entry under `projects` with `id`, `root`, `label`, and optional
`default_include_globs` / `deny_globs`.

## Service usage

Linux systemd:
- `ops/systemd/meta-agent-watch.service`

Windows NSSM:
- `ops/windows/nssm_install.ps1`

## Exit codes

- `0` allow + applied
- `10` warn (patch/report only)
- `11` dry_run_complete (patch/report only)
- `12` gate_failed (patch/report only)
- `20` block (patch/report only)
- `30` error
- `40` invalid_task
- `50` lock_busy

## Troubleshooting

- **lock_busy (50)**: another Meta-Agent process holds the lock.
- **invalid_task (40)**: check required fields in `task.yaml`.
- **gate_failed (12)**: inspect `runtime/runs/<run_id>/gates/*.out` and `.err`.
- **warn verdict**: use UI or CLI approve/apply to re-run gates before apply.
- **scheduler stuck/backoff**: inspect `runtime/scheduler/state.json` `next_eligible_at` and `attempts`.
- **UI 403**: refresh the page to reload a valid token.
- **Logs**: `runtime/logs/meta_agent.log` and `runtime/logs/control_center.log`.
