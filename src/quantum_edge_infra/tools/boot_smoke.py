"""Minimal boot smoke check for entrypoint syntax."""

from __future__ import annotations

import py_compile
from pathlib import Path


def _compile(path: Path) -> None:
    py_compile.compile(str(path), doraise=True)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    targets = [
        root / "QuantumEdge.py",
        root / "SupervisorAgent" / "supervisor.py",
    ]
    for target in targets:
        _compile(target)
        print(f"[OK] Compiled: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
