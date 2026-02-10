# Stage 0 Hardening Notes

- Removed local secrets from the repo and added `.env.example` placeholders.
- Strengthened secret handling: scanner denylist + LLM prompt masking.
- Enforced safety policy in stage pipeline and unified write engine behavior.
- Added cross-platform run lock for task, stage, and offmarket flows.
- Fixed prompt header encoding and safer prompt chunking by file blocks.
- Added `meta_agent.py diag`, new tests, and a minimal pytest workflow.
