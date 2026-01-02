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
    context_manifest.json
  logs/meta_agent.log
  inbox/
  inbox_done/
  inbox_failed/
```

## Service usage

Linux (systemd): see `ops/systemd/meta-agent-watch.service`.

Windows (NSSM): see `ops/windows/nssm_install.ps1`.

## Exit codes (run-task)

- `0` allow + applied
- `10` warn (patch/report only)
- `20` block (patch/report only)
- `30` error
- `40` invalid_task
- `50` lock_busy

## Troubleshooting

- `lock busy`: another Meta-Agent process is running; wait or stop it.
- `invalid_task`: check required fields in `task.yaml`.
- Logs: `runtime/logs/meta_agent.log`
