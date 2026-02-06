"""Non-interactive smoke runner for shadow/live-demo modes."""

from __future__ import annotations

import argparse
import asyncio
import os
import tempfile
import time
from pathlib import Path


def _write_temp_config(mode: str) -> Path:
    payload = (
        "app:\n"
        f"  mode: \"{'demo' if mode == 'live-demo' else 'paper'}\"\n"
        "  websocket: \"mock\"\n"
        "  log_level: \"INFO\"\n"
        "  llm_enabled: false\n"
        "execution:\n"
        "  mode: \"normal\"\n"
        f"  shadow: {str(mode != 'live-demo').lower()}\n"
        "telemetry:\n"
        "  enabled: false\n"
        "ops:\n"
        "  status_file: \"state/bot_status.json\"\n"
    )
    fd, path = tempfile.mkstemp(prefix="qe_smoke_", suffix=".yaml")
    with open(fd, "w", encoding="utf-8") as handle:
        handle.write(payload)
    return Path(path)


async def _run(minutes: float) -> int:
    from bot.run_bot import main as run_bot

    stop_event = asyncio.Event()
    duration = max(minutes, 0.1) * 60.0
    task = asyncio.create_task(run_bot(stop_event=stop_event))
    try:
        await asyncio.sleep(duration)
    finally:
        stop_event.set()
        try:
            await asyncio.wait_for(task, timeout=10.0)
        except asyncio.TimeoutError:
            task.cancel()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="QuantumEdge smoke runner")
    parser.add_argument("--mode", default="shadow", choices=["shadow", "live-demo"])
    parser.add_argument("--minutes", type=float, default=2.0)
    args = parser.parse_args()

    temp_cfg = _write_temp_config(args.mode)
    os.environ["QE_CONFIG_PATH"] = str(temp_cfg)
    start = time.time()
    code = asyncio.run(_run(args.minutes))
    elapsed = time.time() - start
    print(f"[SMOKE] Completed in {elapsed:.1f}s (mode={args.mode})")
    try:
        temp_cfg.unlink()
    except Exception:
        pass
    return code


if __name__ == "__main__":
    raise SystemExit(main())
