# AlmaLinux Runbook (QuantumEdge)

## Prereqs

- AlmaLinux 9.x
- Python 3.11+
- Git

## Install system dependencies

```bash
sudo dnf install -y python3 python3-devel git
```

## Install QuantumEdge

```bash
sudo mkdir -p /opt/quantumedge
sudo chown -R $USER:$USER /opt/quantumedge
git clone https://github.com/Leonidas2035/QuantumEdge.git /opt/quantumedge
cd /opt/quantumedge
./scripts/linux/setup.sh
```

## Environment file

Create `/etc/quantumedge/quantumedge.env` (not tracked in git):

```bash
sudo mkdir -p /etc/quantumedge
sudo nano /etc/quantumedge/quantumedge.env
```

Example (no secrets shown):

```
QE_ROOT=/opt/quantumedge
SUPERVISOR_HOST=127.0.0.1
SUPERVISOR_PORT=8765
```

## Systemd service

Install and enable:

```bash
cd /opt/quantumedge
sudo ./deploy/systemd/install.sh
sudo systemctl enable --now quantumedge-supervisor.service
```

Status and logs:

```bash
sudo systemctl status quantumedge-supervisor.service
sudo journalctl -u quantumedge-supervisor.service -f
```

## Manual run (foreground)

```bash
cd /opt/quantumedge
./scripts/linux/run_supervisor.sh
```
