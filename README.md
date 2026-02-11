# QuantumEdge

QuantumEdge is a high-frequency trading system and agentic development platform running on Google Antigravity. It features a modular architecture designed for low-latency market data processing, ML-driven scalping strategies, and autonomous change orchestration via Meta-Agent.

## Project Structure (Src-Layout)

The repository follows a `src/` layout for improved modularity and maintainability:

- **`src/quantum_edge_core/`**: Core trading logic and services.
    - **`market_data/`**: Ingestion, normalization, and distribution of market data. Entry point: `src/quantum_edge_core/market_data/hub.py`.
    - **`supervisor/`**: Risk management, process monitoring, and policy enforcement. Entry point: `src/quantum_edge_core/supervisor/supervisor.py`.
    - **`ai_scalper_bot/`**: High-frequency scalping bot with ML integration. Entry point: `src/quantum_edge_core/ai_scalper_bot/run_bot.py`.
- **`src/quantum_edge_infra/`**: Infrastructure, automation, and tooling.
    - **`automation/meta_agent/`**: Orchestrator for safety-gated code changes. Entry point: `src/quantum_edge_infra/automation/meta_agent/meta_agent.py`.
- **`src/quantum_edge_ml/`**: Machine learning models and inference engines.

## Quickstart

The system is managed by the `QuantumEdge.py` orchestrator.

```bash
# Start all services (Supervisor, MarketDataHub, Bot)
python QuantumEdge.py start

# Start with Meta-Agent
python QuantumEdge.py start --with-meta

# Check system status
python QuantumEdge.py status

# Run Meta-Agent diagnostics
python meta_agent.py diag
```

## Meta-Agent

Meta-Agent is a controlled, safety-gated change orchestrator for this monorepo.
It scans project context, builds prompts, and produces structured change sets.
All writes go through a single safety policy and write engine.

### Meta-Agent Quickstart

```bash
python meta_agent.py diag
python meta_agent.py health
python meta_agent.py run-task --task examples/tasks/001_refactor_small.yaml
python meta_agent.py watch --inbox runtime/inbox --poll-seconds 2
python meta_agent.py ui
```

## Results and artifacts

- Report: `runtime/runs/<run_id>/report.json`
- Patches: `runtime/runs/<run_id>/patches/`
- Gates output: `runtime/runs/<run_id>/gates/`
- Shadow workspace: `runtime/runs/<run_id>/shadow/`
- Change set: `runtime/runs/<run_id>/changeset.json`

## Safety model

- Default write mode is patch-only unless policy allows direct apply.
- Safety policy evaluates every change set before any write.
- Shadow + gates must pass before allow applies to the real repo.
- Approve/apply is allowed only for `warn` verdicts and reruns gates.

## Docs

- Architecture: `docs/architecture.md`
- Operations runbook: `docs/operations.md`
- Task contract: `docs/tasks_contract.md`
- LLM engine (Stage 1): `src/quantum_edge_ml/inference_engine/README.md`
- Scheduler: `docs/scheduler.md`
- Control Center UI: `docs/control_center.md`
- Security: `docs/security.md`
- Spot scalper: `docs/spot_scalper.md`
- Release notes: `docs/CHANGELOG.md`
- Upgrade guide: `docs/upgrade.md`
