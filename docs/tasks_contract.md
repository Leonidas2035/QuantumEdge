# Task Contract (TaskSpec + Report)

This document defines the TaskSpec input format and the Report output for the Meta-Agent task pipeline.

## TaskSpec (task.yaml)

Required fields:
- `task_id` (string, generated if missing)
- `created_at` (ISO 8601 string)
- `project_id` (logical project id from registry)
- `objective` (short goal)
- `instructions` (detailed requirements)
- `mode` (must be `task`)

Optional fields:
- `project_root` (relative path under repo root; overrides registry)
- `constraints`:
  - `patch_only` (bool)
  - `max_files` (int)
  - `max_file_bytes` (int)
  - `deny_globs` (list of glob patterns)
- `context`:
  - `include_globs` (list of glob patterns)
  - `focus_files` (list of files to prioritize)
- `llm`:
  - `model` (string)
  - `temperature` (float)
  - `max_context_chars` (int)
- `metadata` (free-form dict for Supervisor)

### Example task.yaml

```yaml
task_id: example_001
created_at: 2026-01-02T22:30:00Z
project_id: meta_agent
project_root: meta_agent
objective: "Update task contract docs"
instructions: |
  Add a short note explaining the contract lifecycle.
constraints:
  patch_only: true
  max_files: 5
  max_file_bytes: 65536
context:
  include_globs:
    - docs/**/*.md
  focus_files:
    - docs/tasks_contract.md
llm:
  model: gpt-4.1
  temperature: 0
  max_context_chars: 80000
mode: task
metadata:
  source: supervisor
```

### task.md with frontmatter (optional)

```markdown
---
task_id: example_002
created_at: 2026-01-02T22:31:00Z
project_id: meta_agent
objective: "Update docs"
instructions: "See body"
mode: task
---

Body text can be used as instructions if `instructions` is missing.
```

## Report (report.json)

```json
{
  "run_id": "20260102_223000_ab12cd",
  "task_id": "example_001",
  "started_at": "2026-01-02T22:30:00+00:00",
  "finished_at": "2026-01-02T22:30:12+00:00",
  "verdict": "warn",
  "exit_code": 10,
  "summary": "Safety warnings: patches generated only.",
  "changes": {
    "patches": [
      {
        "path": "docs/tasks_contract.md",
        "patch_file": "runtime/runs/20260102_223000_ab12cd/patches/docs/tasks_contract.md.patch",
        "sha_before": "....",
        "sha_after": "...."
      }
    ],
    "applied": false,
    "files_changed": 1
  },
  "safety": {
    "policy_version": "safety_policy.yaml",
    "checks": ["docs/tasks_contract.md: ..."]
  },
  "artifacts": {
    "report_path": "runtime/runs/20260102_223000_ab12cd/report.json",
    "patches_dir": "runtime/runs/20260102_223000_ab12cd/patches",
    "logs_path": null,
    "context_manifest_path": "runtime/runs/20260102_223000_ab12cd/context_manifest.json",
    "task_path": "runtime/runs/20260102_223000_ab12cd/task.yaml"
  },
  "errors": []
}
```

## Exit codes

- `0`  allow + applied
- `10` warn (patch/report only)
- `20` block (patch/report only)
- `30` error (unexpected)
- `40` invalid task (validation)
- `50` lock busy

## Runtime layout

```
runtime/runs/<run_id>/
  task.yaml
  report.json
  patches/
  context_manifest.json
```

## Supervisor usage

Supervisor should call the CLI with a TaskSpec path and read `report.json` for verdict and artifacts.

Example:
```
python meta_agent.py run-task --task examples/tasks/001_refactor_small.yaml
```
