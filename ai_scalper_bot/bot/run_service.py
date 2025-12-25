"""Service-ready entrypoint for QuantumEdge."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
from pathlib import Path
from typing import Optional

from bot.core.config_loader import Config, config as global_config
from bot.core.logging_setup import setup_logging
from bot.ops.status_writer import BotStatusWriter
from bot import run_bot as bot_main


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="QuantumEdge service runner")
    parser.add_argument("--config", default="config/settings.yaml", help="Path to settings YAML")
    parser.add_argument("--mode", choices=["live", "paper", "demo"], default=None, help="Override app.mode")
    parser.add_argument("--once", action="store_true", help="Run a single loop iteration (debug/testing)")
    parser.add_argument(
        "--allow-no-models",
        action="store_true",
        help="Start in observer mode even if ML models are missing (disables trading).",
    )
    return parser.parse_args()


def _override_config(cfg_path: str, mode: Optional[str], allow_no_models: bool) -> None:
    """Reload global config singleton with overrides."""
    os.environ["QE_CONFIG_PATH"] = cfg_path
    # Replace global config instance to ensure all imports see updated data
    global global_config
    global_config.__dict__.update(Config(cfg_path).__dict__)
    if mode:
        global_config.data.setdefault("app", {})
        global_config.data["app"]["mode"] = mode
    if allow_no_models:
        global_config.data.setdefault("ml", {})
        global_config.data["ml"]["require_models"] = False


def _install_signal_handlers(loop: asyncio.AbstractEventLoop, stop_event: asyncio.Event, logger: logging.Logger) -> None:
    """Attach signal handlers with a Windows-safe fallback."""
    def _trigger(signame: str) -> None:
        logger.info("Received %s, shutting down gracefully...", signame)
        loop.call_soon_threadsafe(stop_event.set)

    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, lambda s=sig_name: _trigger(s))
        except NotImplementedError:
            signal.signal(sig, lambda *_: _trigger(sig_name))


async def _run(args: argparse.Namespace) -> int:
    # Logging setup
    log_dir = Path("logs")
    logger = setup_logging(log_dir / "quantumedge.log", level=global_config.get("app.log_level", "INFO"))

    # Ops status writer
    ops_cfg = global_config.get("ops", {}) or {}
    status_file = Path(ops_cfg.get("status_file", "state/bot_status.json"))
    write_interval = float(ops_cfg.get("write_interval_seconds", 2))
    status_writer = BotStatusWriter(status_file, interval_seconds=write_interval)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    _install_signal_handlers(loop, stop_event, logger)

    try:
        result = await bot_main.main(stop_event=stop_event, once=args.once, status_writer=status_writer, logger=logger)
        return int(result) if result is not None else 0
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception("Service run failed: %s", exc)
        status_writer.flush({"ts": "error", "is_running": False, "last_error": str(exc)})
        return 1


def main() -> None:
    args = parse_args()
    _override_config(args.config, args.mode, args.allow_no_models)
    exit_code = asyncio.run(_run(args))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
