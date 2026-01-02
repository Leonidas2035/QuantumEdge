# Meta-Agent Architecture

This document describes the core components, control flow, and single write-path guarantees.

## Components

- `meta_core.run_task`: orchestrates task execution from TaskSpec to report.
- `task_contract`: TaskSpec/Report schema and validation.
- `project_scanner`: collects file context with denylist + secret masking.
- `codex_client`: LLM request abstraction.
- `write_engine`: single apply path for all changes (patch-only or direct).
- `safety_policy`: evaluates change sets and enforces verdicts.
- `shadow_workspace`: creates a shadow copy/worktree for gating.
- `gate_runner`: executes gate steps and captures stdout/stderr.
- `inbox_processor`: batch execution for inbox (used by watch/scheduler).
- `offmarket_scheduler`: windows + trigger + state/recovery/backoff.
- `control_center`: UI + API server + approve/apply workflow.

## Flow 1: run-task (allow -> gated apply)

```mermaid
sequenceDiagram
    participant CLI as meta_agent.py
    participant Core as meta_core.run_task
    participant Scan as project_scanner
    participant LLM as codex_client
    participant Safety as safety_policy
    participant Shadow as shadow_workspace
    participant Gates as gate_runner
    participant Write as write_engine

    CLI->>Core: run_task(task.yaml)
    Core->>Scan: collect context (denylist + masking)
    Core->>LLM: prompt
    LLM-->>Core: response
    Core->>Safety: evaluate change set
    Core->>Write: write patches (always)
    alt verdict != allow
        Core-->>CLI: report (patch-only)
    else verdict == allow
        Core->>Shadow: create shadow
        Core->>Write: apply to shadow
        Core->>Gates: run gates
        alt gates passed and not dry_run
            Core->>Write: apply to real repo
        else gates failed or dry_run
            Core-->>CLI: report (patch-only)
        end
    end
```

## Flow 2: watch/inbox batch

```mermaid
sequenceDiagram
    participant Watch as watch.py
    participant Inbox as inbox_processor
    participant Core as meta_core.run_task
    Watch->>Inbox: process_inbox_once()
    Inbox->>Core: run_task(task.yaml)
    Core-->>Inbox: Report
    Inbox-->>Watch: archive/failed move
```

## Flow 3: scheduler (windows -> enqueue -> execute -> state)

```mermaid
flowchart TD
    A[Scheduler tick] --> B{STOP/PAUSE?}
    B -->|STOP| Z[Exit]
    B -->|PAUSE| C[Update state only]
    B -->|No| D[Load schedules + state.json]
    D --> E{Within window?}
    E -->|No| C
    E -->|Yes| F{Trigger due?}
    F -->|No| C
    F -->|Yes| G[Enqueue task.yaml in inbox]
    G --> H[process_inbox_once()]
    H --> I[Update state.json + backoff]
```

## Single write-path guarantee

All writes are routed through `write_engine.apply_change_set_with_policy`.
This enforces safety policy verdicts and ensures patch-only mode is respected.
No stage pipeline or UI action bypasses this path, so warn/block never apply.
The approve/apply path reuses the same write engine and safety checks.
