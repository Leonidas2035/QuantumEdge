# QuantumEdge Monorepo

This repository combines multiple projects under a single root for coordinated development.

Projects:
- ai_scalper_bot/
- SupervisorAgent/
- meta_agent/

Shared (kept empty or runtime-only):
- config/
- runtime/
- logs/
- data/

Config:
- See `docs/CONFIG.md` for unified QE_ROOT-based settings and run commands.

Rule: do not commit secrets, API keys, or encrypted secret files. Keep those local and out of git.
