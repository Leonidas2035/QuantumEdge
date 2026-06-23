# Meta-Agent Technical Reference

## Modules
- **meta_agent.py** – CLI entry; parses args; stage pipeline over `stages.yaml`; instantiates `MetaAgent` (PromptBuilder + CodexClient + ProjectScanner + FileManager readonly); `cleanup_after_successful_run` archives prompts and clears stages.
- **meta_gui.py** – Tkinter UI to add prompt (`prompts/` + `stages.yaml`) and run `python meta_agent.py`.
- **meta_core.py** – Task execution core for `run_task`: loads task, builds prompt with `ProjectScanner`, calls `CodexClient`, parses ChangeSet (`file_manager`), evaluates safety (`safety_policy`), applies direct or emits patches, runs basic QC (py_compile + optional pytest), writes reports.
- **codex_client.py** – OpenAI wrapper using `OPENAI_API_KEY_<MODE>`; chunked prompts (12k chars each) to `gpt-4.1`; returns model content or `[ERROR] ...` string.
- **project_scanner.py** – Collects project context with include/ext filters and directory excludes; enforces per-file (100k chars) and total (~250k by default) caps; tracks stats.
- **projects_config.py** – Loads/creates project registry from `config/projects.yaml` (default auto-written); resolves project_id → absolute path; provides default project id.
- **prompt_builder.py** – Assembles final prompt sections: header, metadata, instructions, context, output guidance.
- **file_manager.py** – Legacy `process_output` to write `===FILE:` blocks (redirects to `output/` in readonly); ChangeSet builder (`build_change_set_from_response`), direct apply (`apply_change_set_direct`), and patch generation (`write_change_set_as_patches`).
- **task_schema.py** – Task parser (header KEY: VALUE + markdown body); validates required fields; defines `Task` dataclass.
- **task_manager.py** – Create/list/load tasks in `tasks/`; task_id generation; simple slugging; wraps `task_schema`.
- **task_archiver.py** – Archives prompt files from stages/tasks into `prompts/archive/<job>/`; clears `stages.yaml` when needed.
- **report_schema.py** – `Report` dataclass; JSON/Markdown writers to `reports/`; helper paths.
- **safety_policy.py** – Loads safety policy YAML or defaults; evaluates ChangeSet (protected/warning/allowed paths, max files/size); returns verdict and write mode.
- **supervisor_runner.py** – Supervisor cycle: plan tasks via LLM, create tasks, execute via `run_task`, aggregate results/candidate changes, write MD/JSON summaries to `reports/supervisor/`.
- **offmarket_config.py / offmarket_scheduler.py / offmarket_runner.py** – Load schedule config/state, check bot idle, compute due maintenance windows, trigger supervisor cycles, persist state.
- **strategy_agent.py** – Builds strategic backlog from recent supervisor summaries (LLM) and can materialize them as tasks.
- **paths.py** – Central paths (`stages.yaml`, prompts/, output/, reports/, tasks/, patches/) and ensures dirs exist.

## Configuration & Directories
- **config.json** – Misc config (e.g., `project_root`, `mode`); used by meta_agent/meta_core.
- **config/projects.yaml** – Project registry (default auto-generated); defines `default` and per-project `path`.
- **config/offmarket_schedule.yaml**, **state/offmarket_state.json** – Scheduler config/state (off-market maintenance).
- **config/safety_policy.yaml** (optional) – Overrides default safety policy.
- **stages.yaml** – Ordered list of stage entries `{name, prompt[, project]}`.
- **prompts/** – Stage prompt files; archived into `prompts/archive/`.
- **tasks/** – Task files for task mode and supervisor-generated tasks.
- **reports/** – Task reports; `reports/supervisor/` for supervisor summaries; moved into `output/` after successful stage runs.
- **output/** – Primary artifact directory for stage outputs and collected reports.
- **patches/** – Generated patch files when safety policy selects `patch_only`.
- **logs/** – Created by offmarket runner for scheduler logs (offmarket_scheduler.log).

## Environment / Secrets
- OpenAI key via `OPENAI_API_KEY_DEV` or `OPENAI_API_KEY_PROD` (selected by `META_AGENT_MODE` or provided mode).
- Optional `META_AGENT_MODE` (`dev`|`prod`) influences API key selection; `config.json` `mode` is fallback.
- Safety policy and project registry are file-based; no secrets checked into repo. Do not store secrets in prompts/tasks/context.
