"""CLI helper to print the current feature schema contract."""

from __future__ import annotations

from quantum_edge_core.strategies.scalper_v1.bot.ml.features.builder import (
    feature_names,
    schema_hash,
    schema_version,
)


def main() -> int:
    names = feature_names()
    print(f"schema_version={schema_version()}")
    print(f"schema_hash={schema_hash()}")
    print(f"feature_count={len(names)}")
    if names:
        head = ", ".join(names[:5])
        tail = ", ".join(names[-5:]) if len(names) > 5 else ""
        print(f"first_5={head}")
        if tail:
            print(f"last_5={tail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
