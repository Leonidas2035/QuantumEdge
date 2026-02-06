# Linux systemd deployment

Templates: `quantumedge.service` (bot) and `supervisoragent.service` (SupervisorAgent).

1) Review unit files and adjust WorkingDirectory if repos are elsewhere.
2) Create env files (sudo):
   - `/etc/quantumedge/quantumedge.env` with `BOT_ENTRYPOINT` (e.g., `run_bot.py --mode paper`)
   - `/etc/quantumedge/supervisor.env` with `SUP_ENTRYPOINT` (e.g., `supervisor.py run-foreground`)
3) Install:
```bash
sudo ./install_systemd.sh
```
4) Check status:
```bash
sudo systemctl status quantumedge.service
sudo systemctl status supervisoragent.service
```
5) Uninstall:
```bash
sudo ./uninstall_systemd.sh
```

Logs are in journald by default (`journalctl -u quantumedge.service -f`). Adjust Python logging config in the repos for file outputs if needed.
