"""Deprecated wrapper. Moved to SupervisorAgent.research.backtest.metrics."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path


def _find_qe_root() -> Path:
    here = Path(__file__).resolve()
    for parent in [here] + list(here.parents):
        if (parent / "QuantumEdge.py").exists():
            return parent
    return here.parents[-1]


def _ensure_sys_path() -> None:
    root = _find_qe_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    bot_dir = root / "ai_scalper_bot"
    if bot_dir.exists() and str(bot_dir) not in sys.path:
        sys.path.insert(0, str(bot_dir))


def _target():
    _ensure_sys_path()
    return importlib.import_module("SupervisorAgent.research.backtest.metrics")


def __getattr__(name):
    return getattr(_target(), name)


def main():
    target = _target()
    if hasattr(target, "main"):
        return target.main()
    raise SystemExit("No CLI entrypoint in SupervisorAgent.research.backtest.metrics")


if __name__ == "__main__":
    main()
