# Meta-Agent Audit Report

## Scope
- Reviewed core orchestration and safety layers: meta_agent.py, meta_core.py, meta_gui.py, codex_client.py, project_scanner.py, projects_config.py, file_manager.py, prompt_builder.py, task_schema.py, task_manager.py, report_schema.py, safety_policy.py, supervisor_runner.py, offmarket_config.py/offmarket_scheduler.py/offmarket_runner.py, strategy_agent.py, task_archiver.py, paths.py, config/projects.yaml, config.json, stages.yaml.
- No code changes were made; this is a read-only assessment.

## Strengths
- Clear separation between stage mode (stages.yaml + prompts) and task mode (tasks/ + reports), with cleanup/archiving of prompts after successful runs.
- Context gathering is capped (~250k chars) and skips common noisy dirs/extensions; per-file cap helps avoid huge blobs.
- Safety layer exists for task mode: safety policy + patch-only default, compile/test checks, report generation (MD + JSON) and patch output support.
- Multi-project registry present (config/projects.yaml) and task materialization helpers (task_manager, task_archiver) keep task files structured.

## Issues & Risks by Module

### meta_agent.py (stage pipeline)
- Severity: **medium** – Stage pipeline writes model output through `FileManager(..., mode="readonly", target_project=None)`, so responses always go to `output/` and never into the target project or safety policy flow. Users may expect direct application. **Suggested fix:** either apply via ChangeSet + safety in stages mode or clearly document that stages mode only emits artifacts for manual review.
- Severity: **medium** – Default project for stages comes from config/projects.yaml (defaults to `ai_scalper_bot`); GUI cannot override per-stage project. On hosts without that repo, stages fail or pull context from an unintended path. **Suggested fix:** add project selector/validation in GUI and stage entries.
- Severity: **medium** – Context cap is 250k chars but the prompt is sent as multiple 12k chunks without an overall token budget; long contexts can exceed model limits and trigger 400 errors. **Suggested fix:** estimate tokens and trim/ summarize context before sending.

### meta_core.py / safety_policy.py
- Severity: **high** – Safety policy is global and defaults to `ai_scalper_bot` paths; tasks for other projects inherit the same whitelist/allowlist and write mode, leading to false blocks or under-protection. **Suggested fix:** load policy per project or require policy selection; fail fast if policy project mismatches target project.
- Severity: **medium** – `_resolve_target_project` trusts relative paths/config.json and will create new directories on apply if the path is wrong; no existence check before writing. **Suggested fix:** validate target_project exists and abort otherwise.
- Severity: **low** – `run_basic_quality_checks` auto-runs pytest whenever `tests/` exists; can be slow/flaky and marks tasks partial. **Suggested fix:** make QC optional/tunable per task or config.

### meta_gui.py
- Severity: **medium** – GUI adds stages without project selection and minimal validation; users cannot control which project is scanned. **Suggested fix:** add a dropdown sourced from project registry and warn when default project path is missing.

### codex_client.py
- Severity: **medium** – Hard failure when API key env var (`OPENAI_API_KEY_<MODE>`) is absent; no retry/backoff and `max_tokens` fixed at 4096 regardless of prompt size. **Suggested fix:** clearer error with key name, optional retries, and adaptive max_tokens/token budgeting.

### project_scanner.py / projects_config.py
- Severity: **medium** – Scanner includes `.json/.txt` etc.; may pull secrets (config, tokens) into prompts. **Suggested fix:** extend default excludes (e.g., *.env, secrets*, keys*) and allow per-project include/exclude config.
- Severity: **low** – `_ensure_default_config` auto-writes config/projects.yaml pointing to external repos (`../ai_scalper_bot`, `../Supervisor agent`) and sets default to ai_scalper_bot even if absent. **Suggested fix:** prompt the user or fail if paths do not exist.

### file_manager.py
- Severity: **medium** – Stage flow still uses deprecated `process_output` (regex extraction) without safety checks or path validation beyond redirection to `output/`. Misformatted blocks are silently skipped. **Suggested fix:** switch stages to ChangeSet parsing + safety policy, add validation/errors on malformed blocks.

### supervisor_runner.py / offmarket_*
- Severity: **medium** – Off-market runner fails if config/state files are missing; `require_bot_idle=True` with missing `bot_status_file` causes perpetual skip without clear guidance. **Suggested fix:** better defaults/messages and explicit “bot status file not found” handling.
- Severity: **low** – Supervisor planning/execution uses CodexClient without retries; failed LLM calls fall back heuristically but errors are not surfaced to the caller. **Suggested fix:** surface planning errors and allow retry/backoff.

## Recommended Priorities
- **Must fix:** per-project safety policy/target project validation in task mode; clarify/apply safety for stage pipeline or document it as artifact-only; handle missing bot status/config clearly.
- **Should fix:** add project selection/validation in GUI and stages; enforce token budgeting/trimming before LLM calls; harden scanner excludes for secrets; improve API key error messaging and optional retries.
- **Nice to have:** toggle quality checks, richer logging for malformed file blocks, optional supervisor/off-market dry-run mode, and config-driven include/exclude for context collection.
