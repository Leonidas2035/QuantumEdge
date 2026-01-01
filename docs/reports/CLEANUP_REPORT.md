# Cleanup Report (Phase 1)

Date: 2025-12-25
Scope: Inventory only. No deletions or moves performed.

## Top-level tree summary
- .git (repo metadata)
- .venv (local root virtual environment)
- .vscode (workspace settings)
- ai_scalper_bot (bot module)
- SupervisorAgent (supervisor module)
- meta_agent (meta-agent module)
- config (shared configs)
- tools (shared tools and CLI helpers)
- scripts (setup/run/secrets helpers)
- requirements (root dependency files)
- docs (repo docs)
- tests (test helpers)
- runtime, logs, data (shared runtime dirs)
- QuantumEdge.py (orchestrator)
- QuantumEdge.code-workspace, README.md, .gitignore

## Scan results (safe inventory)
- Nested venvs: only root `.venv` found.
- __pycache__ dirs and *.pyc files: present in `.venv` and module trees.
- Secret-like files (outside `.venv`):
  - No `*.env`, `secrets.env`, `*.enc`, or `backup_secrets*` found.
  - Templates found: `config/env.example`, `config/secrets.local.env.example`,
    `ai_scalper_bot/config/secrets.env.example`, `meta_agent/infra/ops/windows/windows.env.example`.
- Nested .git: only `C:\QuantumEdge\.git`.
- Large files (>20MB):
  - `.venv/Lib/site-packages/xgboost/lib/xgboost.dll` (106,728,960 bytes)
  - `ai_scalper_bot/python-3.12.10-amd64.exe` (26,964,224 bytes)
  - `ai_scalper_bot/Output/ai_scalper_bot_installer.exe` (37,215,233 bytes)
  - `ai_scalper_bot/Output/ai_scalper_bot_installer.rar` (36,702,769 bytes)
- Duplicate module folder: `meta_agent/repo_meta_agent`.
- Output/build artifact dirs:
  - `ai_scalper_bot/Output`
  - `meta_agent/output`
  - `SupervisorAgent/output`
- Config references (not safe to delete yet):
  - `config/paths.yaml` referenced by `tools/qe_config.py`, `tools/qe_cli.py`,
    `SupervisorAgent/supervisor.py`, and docs.
  - `config/projects.yaml`, `config/offmarket_schedule.yaml`, `config.json` referenced
    by `meta_agent` code and docs.

## Candidates to remove (proposed, pending confirmation)
- `__pycache__/` and `*.pyc` across module trees and `.venv` (compiled caches).
- `.venv/` (local dev environment; should not be committed).
- `ai_scalper_bot/Output/` and installer archives/exe (build artifacts).
- `ai_scalper_bot/python-3.12.10-amd64.exe` (installer artifact).
- `meta_agent/output/` and `SupervisorAgent/output/` (build artifacts).
- `meta_agent/repo_meta_agent/` (appears to be a duplicate module folder; needs validation).

## Candidates to move (proposed, pending confirmation)
- Legacy or duplicate env templates:
  - `ai_scalper_bot/config/secrets.env.example` -> `docs/legacy/env/`
  - `meta_agent/infra/ops/windows/windows.env.example` -> `docs/legacy/env/`
- Module-specific docs that may duplicate root docs:
  - `SupervisorAgent/Documentation/` and `meta_agent/Documentation/` -> `docs/legacy/`
  - Needs content review first; do not move without confirming no conflicts.

## Candidates to keep (core runtime)
- `QuantumEdge.py`, `tools/`, `scripts/`, `requirements/`, `.vscode/`
- `config/` (including `quantumedge.yaml`, `supervisor.yaml`, `bot.yaml`,
  `meta_agent.yaml`, `paths.yaml`)
- `ai_scalper_bot/`, `SupervisorAgent/`, `meta_agent/`
- `docs/CONFIG.md`, `README.md`
- `runtime/`, `logs/`, `data/` (keep empty with `.gitkeep` later)

## Next actions (Phase 2+)
- Confirm which candidates are safe to remove or move via reference checks.
- Use `git mv` for any moves.
- Remove build artifacts and caches only after confirmation.
