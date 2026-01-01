# Meta-Agent Runbook

## Before You Start
- Install dependencies (`pip install -r requirements.txt`) and ensure `python` in PATH.
- Set OpenAI key env var: `OPENAI_API_KEY_DEV` (or `_PROD`), optionally `META_AGENT_MODE` (`dev`/`prod`).
- Verify `config/projects.yaml` points to existing repos and default project is what you intend. For task mode, `config.json` `project_root` is used if set.

## Create a Task via GUI (stages mode)
- Run `python meta_gui.py`.
- Fill **Task name/title** and paste instructions in the prompt area.
- Click **Add**: saves prompt as `prompts/stage_XX_<slug>.md` and appends an entry to `stages.yaml` (project defaults from registry; GUI currently lacks project selector).
- Click **Start**: launches `meta_agent.py` once in stages mode.

## What Happens on Start (stages mode)
- `meta_agent.py` loads `stages.yaml`, resolves the project (default from `config/projects.yaml` unless stage specifies `project`), and scans that project via `ProjectScanner` (filters + size caps).
- Builds prompt with metadata + context, sends to LLM via `CodexClient`.
- Model output `===FILE:` blocks are written to `output/` (readonly mode; no direct writes to target project).
- On success: prompts are archived to `prompts/archive/`, `stages.yaml` is cleared, and any reports under `reports/` are moved into `output/`.

## Task Mode (CLI)
- Place a task file in `tasks/` or use `--task-id` to refer to `<id>.md`.
- Run `python meta_agent.py --mode task --task-id <TASK_ID>` (or `--task <path>`).
- Flow: load task → scan target project (from task.project, or `config.json` project_root) → build prompt → LLM → ChangeSet parsed → safety policy applied (patch/direct) → optional QC (py_compile/pytest) → reports written to `reports/` (+ patches in `patches/`).
- Listing tasks: `python meta_agent.py --list-tasks [--project <id>] [--task-type <type>]`.

## Supervisor / Off-Market
- Supervisor cycle: `python meta_agent.py --supervisor-goal "<goal>" [--mode daily|weekly|adhoc] [--supervisor-project <id>]` plans tasks, executes them, and writes summaries to `reports/supervisor/`.
- Off-market one-shot scheduler: `python offmarket_runner.py [--config <path>] [--state <path>]`; requires `config/offmarket_schedule.yaml` and optional bot status file if `require_bot_idle` is true. Logs go to `logs/offmarket_scheduler.log`.

## Where to Find Results
- Stage outputs: `output/` (files from model responses) and archived prompts in `prompts/archive/`.
- Task mode reports: `reports/<task>_report.md|json` (may be moved to `output/` after a stage run); patches in `patches/`.
- Supervisor summaries: `reports/supervisor/`.

## Troubleshooting
- **Missing API key / auth errors:** ensure `OPENAI_API_KEY_DEV/PROD` is set for the chosen mode.
- **Context too large / 400 errors:** trim instructions/context or exclude noisy dirs/files; reduce stage count.
- **Default project not found:** update `config/projects.yaml` (stages) or `config.json` (task mode) to existing paths; GUI currently always uses the registry default.
- **Bot idle requirement blocks off-market:** set `require_bot_idle` to false or provide a valid `bot_status_file` JSON with `is_trading` and `open_positions`.
- **Unexpected skips/blocks by safety policy:** policy defaults to `ai_scalper_bot`; add/adjust `config/safety_policy.yaml` to match the target project or pass tasks to a project with a matching policy.
- **Test failures slowing runs:** disable or adjust QC in code/config if tests are flaky; inspect `report.meta.quality_checks.tests_output`.
