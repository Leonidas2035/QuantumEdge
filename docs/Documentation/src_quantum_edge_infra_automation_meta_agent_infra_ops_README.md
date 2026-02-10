# Operational deployment toolkit

This directory contains platform-specific helpers to run QuantumEdge (ai_scalper_bot), SupervisorAgent, and optional TSDB stacks as services.

- **Windows (NSSM)**: `infra/ops/windows/`
  - Copy `windows.env.example` to `windows.env` and fill paths (Python, bot repo, supervisor repo, entrypoints, ports, API keys if needed).
  - Place `nssm.exe` alongside the scripts or ensure it is on `PATH`.
  - Install services: `.\install_services.ps1`
  - Start/stop: `.\start_services.ps1` / `.\stop_services.ps1`
  - Uninstall: `.\uninstall_services.ps1`
- **Linux (systemd)**: `infra/ops/linux/`
  - Edit unit templates or supply env files under `/etc/quantumedge/quantumedge.env` and `/etc/quantumedge/supervisor.env`.
  - Install: `sudo ./install_systemd.sh`
  - Uninstall: `sudo ./uninstall_systemd.sh`

TSDB stacks (QuestDB, ClickHouse, Timescale) are provided via Docker Compose in `infra/tsdb/`. See that README for details.
