# Windows 11 Launch Checklist (QuantumEdge)

Target Python: 3.12.x

## Setup
1) Create and activate venv (from repo root):
   - `py -3 -m venv .venv`
   - `.\\.venv\\Scripts\\Activate.ps1`
2) Install dependencies:
   - `python -m pip install --upgrade pip`
   - `python -m pip install -r requirements\\requirements.txt`

## Run (recommended)
- Start: `python QuantumEdge.py start`
- Status: `python QuantumEdge.py status`
- Diag: `python QuantumEdge.py diag`
- Stop: `python QuantumEdge.py stop`

## Supervisor foreground (debug)
- `python SupervisorAgent\\supervisor.py run-foreground`

## Port check (Supervisor API)
- `netstat -ano | findstr 8765`

## Common errors
- IndentationError/SyntaxError: run `python -m py_compile SupervisorAgent\\supervisor.py`
- Missing config YAMLs: ensure `config\\supervisor.yaml` and `config\\bot.yaml` exist
- Port already in use: stop the existing process (use PID from netstat)
- Missing runtime/artifacts dirs: run `python QuantumEdge.py diag` or create `runtime\\` and `artifacts\\`
