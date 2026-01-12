# QuantumEdge Meta-Agent

Meta-Agent is a controlled, safety-gated change orchestrator for this monorepo.
It scans project context, builds prompts, and produces structured change sets.
All writes go through a single safety policy and write engine.
Allow changes can be gated by shadow workspaces and quality gates.
Warn/block results never apply automatically; patches and reports are saved.
Dry-run mode always produces patches without applying to real repos.
Runs are archived under `runtime/runs/<run_id>/` with full artifacts.
An inbox/watch loop supports batch execution without parallel workers.
An off-market scheduler creates tasks in maintenance windows (Europe/Kyiv).
A local Control Center UI provides task creation and run review.
Manual approve/apply re-runs shadow+gates before any real apply.
All entrypoints return stable exit codes for automation.

## Quickstart

```bash
python meta_agent.py diag
python meta_agent.py health
python meta_agent.py run-task --task examples/tasks/001_refactor_small.yaml
python meta_agent.py watch --inbox runtime/inbox --poll-seconds 2
python meta_agent.py run-scheduler --once
python meta_agent.py ui
```

## Results and artifacts

- Report: `runtime/runs/<run_id>/report.json`
- Patches: `runtime/runs/<run_id>/patches/`
- Gates output: `runtime/runs/<run_id>/gates/`
- Shadow workspace: `runtime/runs/<run_id>/shadow/`
- Change set: `runtime/runs/<run_id>/changeset.json`

## Safety model

- Default write mode is patch-only unless policy allows direct apply.
- Safety policy evaluates every change set before any write.
- Shadow + gates must pass before allow applies to the real repo.
- Approve/apply is allowed only for `warn` verdicts and reruns gates.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | allow + applied |
| 10 | warn (patch/report only) |
| 11 | dry_run_complete (patch/report only) |
| 12 | gate_failed (patch/report only) |
| 20 | block (patch/report only) |
| 30 | error |
| 40 | invalid_task |
| 50 | lock_busy |

## Docs

- Architecture: `docs/architecture.md`
- Operations runbook: `docs/operations.md`
- Task contract: `docs/tasks_contract.md`
- LLM engine (Stage 1): `llm_engine/README.md`
- Scheduler: `docs/scheduler.md`
- Control Center UI: `docs/control_center.md`
- Security: `docs/security.md`
- Spot scalper: `docs/spot_scalper.md`
- Release notes: `docs/CHANGELOG.md`
- Upgrade guide: `docs/upgrade.md`
