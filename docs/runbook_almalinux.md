# AlmaLinux Runbook (QuantumEdge)

## Prereqs

- AlmaLinux 9.x
- Python 3.11+ (recommended)
- Git

## Install system dependencies

```bash
sudo dnf install -y python3 python3-venv python3-devel gcc gcc-c++ make git
```

## Clone location (recommended)

```bash
sudo mkdir -p /opt/QuantumEdge
sudo chown -R "$USER:$USER" /opt/QuantumEdge
git clone https://github.com/Leonidas2035/QuantumEdge.git /opt/QuantumEdge
cd /opt/QuantumEdge
```

## Setup (venv + deps)

```bash
./scripts/linux/setup.sh
```

## Environment file (optional)

Create `/etc/quantumedge/env` (not tracked in git):

```bash
sudo mkdir -p /etc/quantumedge
sudo nano /etc/quantumedge/env
```

Example (placeholders only):

```
QE_ROOT=/opt/QuantumEdge
SUPERVISOR_HOST=127.0.0.1
SUPERVISOR_PORT=8765
BINANCE_MODE=paper
BINANCE_USE_TESTNET=1
BINANCE_API_KEY=REPLACE_ME
BINANCE_API_SECRET=REPLACE_ME
```

## Run foreground (canonical)

```bash
./scripts/linux/run_supervisor.sh run-foreground --episode-set smoke --scenario-id S00
```

Equivalent direct command:

```bash
python SupervisorAgent/supervisor.py run-foreground --episode-set smoke --scenario-id S00
```

## Systemd service

Install the unit (does not auto-start):

```bash
sudo ./deploy/systemd/install.sh
```

Enable and start:

```bash
sudo systemctl enable quantumedge-supervisor.service
sudo systemctl start quantumedge-supervisor.service
```

Status and logs:

```bash
sudo systemctl status quantumedge-supervisor.service
sudo journalctl -u quantumedge-supervisor.service -f
```

Notes:
- Customize `ExecStart` args in `deploy/systemd/quantumedge-supervisor.service` if you need a different scenario.
- For least privilege, run the service under a dedicated user and ensure `/opt/QuantumEdge` and `/etc/quantumedge` are readable.

## Troubleshooting

- Missing venv: rerun `./scripts/linux/setup.sh`.
- Permission denied: check ownership of `/opt/QuantumEdge` and `/etc/quantumedge`, or run with sudo where needed.
- Port in use: adjust `SUPERVISOR_PORT` in `/etc/quantumedge/env`.
- Verify process: `pgrep -a -f supervisor.py` or `systemctl status quantumedge-supervisor.service`.
- Logs: `journalctl -u quantumedge-supervisor.service -f` or `logs/supervisor.log` (if configured).
