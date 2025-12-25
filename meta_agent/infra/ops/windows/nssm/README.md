# NSSM setup (Windows)

NSSM is not bundled. Download from https://nssm.cc/download and place `nssm.exe` in a folder included in `PATH` or set `NSSM_EXE` in `windows.env` to the full path.

Scripts in the parent directory expect `nssm.exe` to install and manage services:
- QuantumEdgeBot (ai_scalper_bot)
- SupervisorAgent

Logs are directed to files under `LOG_DIR` from `windows.env`.*** End Patch?>">
