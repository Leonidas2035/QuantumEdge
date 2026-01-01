"""Canonical CLI entrypoint for QuantumEdge."""

from __future__ import annotations

from tools import qe_cli


def main() -> int:
    return qe_cli.main()


if __name__ == "__main__":
    raise SystemExit(main())
