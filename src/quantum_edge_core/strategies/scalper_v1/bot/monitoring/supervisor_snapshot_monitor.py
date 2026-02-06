"""Background monitor for SupervisorAgent snapshots."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

from bot.integrations.supervisor_snapshot_client import SupervisorSnapshotClient, SupervisorSnapshot
from bot.core.config_loader import SupervisorSnapshotsSettings


async def run_supervisor_snapshot_monitor(settings: SupervisorSnapshotsSettings, client: SupervisorSnapshotClient, logger: logging.Logger) -> None:
    """Poll snapshots periodically for observability only."""

    if not settings.enabled:
        return

    poll_interval = max(1, settings.poll_interval_seconds)
    log_file_path: Optional[Path] = None
    if settings.log_to_file:
        log_file_path = Path(settings.log_file)
        log_file_path.parent.mkdir(parents=True, exist_ok=True)

    while True:
        try:
            snapshot = await client.fetch_snapshot()
            if snapshot:
                _log_snapshot(snapshot, settings, logger, log_file_path)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - background safety
            logger.warning("Snapshot monitor error: %s", exc)
        await asyncio.sleep(poll_interval)


def _log_snapshot(snapshot: SupervisorSnapshot, settings: SupervisorSnapshotsSettings, logger: logging.Logger, log_file_path: Optional[Path]) -> None:
    ts = snapshot.timestamp.isoformat() if snapshot.timestamp else "unknown"
    line = (
        f"[{ts}] trend={snapshot.trend} conf={snapshot.trend_confidence} "
        f"risk={snapshot.market_risk_level} pnl={snapshot.behavior_pnl_quality} "
        f"signal_quality={snapshot.behavior_signal_quality} flags={','.join(snapshot.behavior_flags)}"
    )
    if settings.log_to_console:
        logger.info("Supervisor snapshot: %s", line)
    if log_file_path:
        try:
            with log_file_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except Exception as exc:  # pragma: no cover - file errors
            logger.warning("Failed to write snapshot log: %s", exc)
