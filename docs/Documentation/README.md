# Operations (Meta-Agent)

This folder contains service templates for running the Meta-Agent watch loop.

## Linux (systemd)

1) Copy `ops/systemd/meta-agent-watch.service` to `/etc/systemd/system/`.
2) Adjust `WorkingDirectory` and `EnvironmentFile` to your repo location.
3) Enable and start:
```
sudo systemctl daemon-reload
sudo systemctl enable meta-agent-watch
sudo systemctl start meta-agent-watch
```

## Windows (NSSM)

1) Place `nssm.exe` under `tools/nssm/` or update the script path.
2) Run:
```
powershell -ExecutionPolicy Bypass -File ops/windows/nssm_install.ps1 -InstallDir C:\QuantumEdge -PythonExe C:\Python312\python.exe
```

## Environment

Use `ops/env.example` as a starting point:
- `META_AGENT_RUNTIME_DIR`
- `META_AGENT_LOG_LEVEL`
- `META_AGENT_MODE`
