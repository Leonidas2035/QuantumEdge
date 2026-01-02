# Meta-Agent Operations

## Commands

- `python meta_agent.py diag`
- `python meta_agent.py health`
- `python meta_agent.py run-task --task examples/tasks/001_refactor_small.yaml`
- `python meta_agent.py status --limit 5`
- `python meta_agent.py watch --inbox runtime/inbox --poll-seconds 2 --archive runtime/inbox_done --failed runtime/inbox_failed`

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
