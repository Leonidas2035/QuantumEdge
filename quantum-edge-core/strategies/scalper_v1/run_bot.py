import asyncio
import os
import sys

from bot.core.secret_store import is_supervisor_mode

SUPERVISOR_FLAG = "QUANTUMEDGE_SUPERVISOR_MODE"


def _detect_supervisor_mode() -> bool:
    return is_supervisor_mode()


# Ensure supervisor mode is visible to downstream imports before loading the main app.
if _detect_supervisor_mode():
    os.environ[SUPERVISOR_FLAG] = "1"
    sys.argv = [sys.argv[0]] + [
        arg
        for arg in sys.argv[1:]
        if arg.lower() not in {"--supervisor-mode", "--supervisor_mode"}
        and not arg.lower().startswith("--supervisor-mode=")
    ]

from bot.run_bot import main


if __name__ == "__main__":
    asyncio.run(main())
