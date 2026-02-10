# AGENTS.md

This file contains instructions and rules for agents working on the QuantumEdge project.

## 1. PROJECT OVERVIEW
QuantumEdge is a high-frequency trading system running on the Google Antigravity agentic development platform.

## 2. DIRECTORY STRUCTURE
- `src/`: Source code for the system.
- `docs/Documentation/`: Consolidated documentation and technical reports.
- `tests/`: System and unit tests.

## 3. CODING CONVENTIONS
- Use Ruff for linting (configured in `pyproject.toml`).
- Follow the src-layout structure.

## 4. TESTING
- Run tests using `pytest`.
- Always verify your changes with relevant tests.

## 5. REPOSITORY MIGRATION
- Primary modules are in `src/quantum_edge_core`, `src/quantum_edge_infra`, and `src/quantum_edge_ml`.

## 6. SAFETY RULES
- All changes are validated in shadow workspaces and quality gates.

## 7. DOCUMENTATION MAINTENANCE RULE
Strict Rule: Any code modification, module refactoring, or architectural change MUST be accompanied by an update to the corresponding documentation. If a module changes, its specific documentation in `docs/Documentation` must be revised. The `FULL_SYSTEM_CONTEXT` file must be kept in sync with the file structure.

## 8. GIT WORKFLOW
Strict Rule: Once any refactoring or task is complete, you MUST commit the changes to git with a descriptive message (e.g., 'Refactor docs structure') and push to origin main.
