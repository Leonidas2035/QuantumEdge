"""Research tooling (offline/backtest/sandbox) for QuantumEdge."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def ensure_qe_root() -> Path:
    root = Path(__file__).resolve().parents[2]
    qe_root = Path(os.getenv("QE_ROOT") or root)
    if str(qe_root) not in sys.path:
        sys.path.insert(0, str(qe_root))
    bot_dir = qe_root / "ai_scalper_bot"
    if bot_dir.exists() and str(bot_dir) not in sys.path:
        sys.path.insert(0, str(bot_dir))
    return qe_root


ensure_qe_root()

