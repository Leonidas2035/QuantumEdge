# Meta-Agent Architecture

## Text Diagram (stages + tasks)
- GUI (`meta_gui.py`) → writes prompt to `prompts/stage_XX_<slug>.md` and appends entry to `stages.yaml`.
- CLI entry (`meta_agent.py`) decides mode:
  - **Stages mode** (default when no task args): load `stages.yaml` → resolve project via `projects_config` → collect context with `ProjectScanner` → build prompt via `PromptBuilder` → send through `CodexClient` → parse `===FILE:` blocks via `FileManager` (readonly → `output/`) → on success run `cleanup_after_successful_run` (archive prompts to `prompts/archive`, clear `stages.yaml`, move reports to `output/`).
  - **Task mode** (`--task/--task-id` or `--mode task`): delegate to `meta_core.run_task` → load task from `tasks/` → context via `ProjectScanner` → prompt → `CodexClient` → parse to ChangeSet → safety policy (`safety_policy.py`) → apply changes or patches → quality checks → write reports (`reports/`, `patches/`).
  - **Supervisor/off-market**: `supervisor_runner.run_supervisor_cycle` plans tasks (LLM) → materializes via `task_manager.create_task` → executes each with `run_task` → aggregates reports; off-market scheduler (`offmarket_scheduler.py`) triggers supervisor cycles on schedule.

## Key Components
- **Runner (meta_agent.py):** CLI, stage pipeline, cleanup/archiving; uses `FileManager` in readonly for stage outputs.
- **GUI (meta_gui.py):** minimal Tk UI to add prompt + stage and start meta_agent.
- **Project scanning:** `project_scanner.py` walks project root with include/ext filters and char caps; `projects_config.py` loads registry (default config auto-created) and resolves project roots.
- **Task/report layer:** `task_schema.py` parser, `task_manager.py` CRUD/listing, `report_schema.py` report writing, `task_archiver.py` prompt archive helpers, `prompt_builder.py` to assemble final prompt, `safety_policy.py` + `file_manager.py` + `meta_core.py` to apply/summarize changes.
- **Supervisor/off-market:** `supervisor_runner.py` plans/executes batches of tasks; `offmarket_config.py`/`offmarket_scheduler.py`/`offmarket_runner.py` schedule maintenance runs; `strategy_agent.py` builds strategic backlog from recent supervisor summaries.

## Data Flow
- GUI/ops create stage prompts → `stages.yaml`.
- Stage run: `meta_agent` reads stages → resolves project → scans project → builds prompt (metadata + instructions + context) → `CodexClient` → model returns `===FILE:` blocks → `FileManager` writes to `output/` → cleanup archives prompts and moves reports.
- Task run: task file in `tasks/` → `run_task` builds prompt with context → `CodexClient` → ChangeSet → safety evaluation → apply direct or emit patches → write reports (`reports/`), patches (`patches/`); `meta_agent` may move reports to `output/` after stages.
- Supervisor/off-market: scheduler decides when to run → supervisor plans tasks (LLM) → tasks created in `tasks/` → executed via `run_task` → summaries in `reports/supervisor/`.

## Multi-Project Support
- Registry in `config/projects.yaml` (default auto-generated) defines `{project_id: path}` and default project; `meta_agent` stages pick project per stage or default. Paths are resolved relative to Meta-Agent root.
- Task mode resolves `task.project` via `meta_core._resolve_target_project`: absolute path wins; otherwise config.json `project_root`, else relative to current working directory. Safety policy is not per-project yet (global default to ai_scalper).
