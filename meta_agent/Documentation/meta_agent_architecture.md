# Meta-Agent Architecture (Stage 7 update)

## Text Diagram (stages + tasks)
- GUI (meta_gui.py) ¢Å' writes prompt to prompts/stage_XX_<slug>.md and appends entry to stages.yaml.
- CLI entry (meta_agent.py) decides mode:
  - **Stages mode** (default when no task args): load stages.yaml ¢Å' resolve project via projects_config ¢Å' collect context with ProjectScanner ¢Å' build prompt via PromptBuilder ¢Å' send through CodexClient ¢Å' parse ===FILE: blocks via FileManager (readonly ¢Å' output/) ¢Å' on success run cleanup_after_successful_run (archive prompts to prompts/archive, clear stages.yaml, move reports to output/).
  - **Task mode** (--task/--task-id or --mode task): delegate to meta_core.run_task ¢Å' load task from 	asks/ ¢Å' context via ProjectScanner ¢Å' prompt ¢Å' CodexClient ¢Å' parse to ChangeSet ¢Å' safety policy (safety_policy.py) ¢Å' apply changes or patches ¢Å' quality checks ¢Å' write reports (eports/, patches/).
  - **Supervisor/off-market**: supervisor_runner plans tasks from supervisor reports; offmarket_scheduler.py checks window/idleness/state and runs the supervisor batch; summaries go to eports/supervisor/ and state/offmarket_state.json.

## Key Components
- **Runner (meta_agent.py):** CLI, stage pipeline, cleanup/archiving; uses FileManager to apply outputs to target project in write_dev mode.
- **GUI (meta_gui.py):** Tk UI to add prompt + stage and start meta_agent, with project dropdown (projects_config registry).
- **Project scanning:** project_scanner.py walks project root with include/ext filters and char caps; projects_config.py loads registry (default config auto-created) and resolves project roots.
- **Task/report layer:** 	ask_schema.py parser, 	ask_manager.py CRUD/listing, eport_schema.py report writing, 	ask_archiver.py prompt archive helpers, prompt_builder.py to assemble final prompt, safety_policy.py + ile_manager.py + meta_core.py to apply/summarize changes.
- **Supervisor/off-market:** supervisor_runner.py builds backlog from supervisor reports and executes tasks; offmarket_scheduler.py enforces window/rate/bot-idle checks using config/offmarket_schedule.yaml and state/offmarket_state.json; offmarket_runner.py is a thin entrypoint.

## Data Flow
- GUI/ops create stage prompts ¢Å' stages.yaml.
- Stage run: meta_agent reads stages ¢Å' resolves project ¢Å' scans project ¢Å' builds prompt (metadata + instructions + context) ¢Å' CodexClient ¢Å' model returns ===FILE: blocks ¢Å' FileManager writes into target project (write_dev) ¢Å' cleanup archives prompts and moves reports.
- Task run: task file in 	asks/ ¢Å' un_task builds prompt with context ¢Å' CodexClient ¢Å' ChangeSet ¢Å' safety evaluation ¢Å' apply direct or emit patches ¢Å' write reports (eports/), patches (patches/); meta_agent may move reports to output/ after stages.
- Supervisor/off-market: scheduler decides when to run ¢Å' supervisor_runner builds backlog from eports/supervisor/ ¢Å' tasks created in 	asks/ ¢Å' executed via un_task ¢Å' summaries in eports/supervisor/ ¢Å' state updated in state/offmarket_state.json.

## Multi-Project Support
- Registry in config/projects.yaml defines {project_id: path, description} and default project; meta_agent stages pick project per stage or default. Paths are resolved relative to Meta-Agent root if not absolute.
- CLI: --list-projects to inspect registry; --project-id to override project for an entire stage run.
- GUI: project dropdown when adding stages.
