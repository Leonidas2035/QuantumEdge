"""Validate ML datasets produced from scenarios."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from quantum_edge_core.strategies.scalper_v1.bot.ml.features.builder import (
    feature_names,
    schema_hash,
)


def _load_json(path: Path) -> Dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _detect_horizons(root: Path, config: Optional[Dict[str, object]]) -> List[int]:
    horizons = []
    if config:
        horizons = list(config.get("label_config", {}).get("horizons", []))
    if not horizons:
        for entry in root.glob("horizon_h*"):
            try:
                horizons.append(int(entry.name.replace("horizon_h", "")))
            except ValueError:
                continue
    return sorted(set(int(h) for h in horizons))


def validate(root: Path) -> int:
    root = root.resolve()
    failures = 0
    checks = 0

    def _check(ok: bool, message: str, level: str = "PASS") -> None:
        nonlocal failures, checks
        checks += 1
        if not ok:
            failures += 1
            print(f"[FAIL] {message}")
        else:
            print(f"[{level}] {message}")

    schema_path = root / "schema.json"
    config_path = root / "config_snapshot.json"
    _check(schema_path.exists(), f"schema.json present: {schema_path}")
    _check(config_path.exists(), f"config_snapshot.json present: {config_path}")

    config = _load_json(config_path) if config_path.exists() else None
    horizons = _detect_horizons(root, config)
    _check(
        bool(horizons),
        f"horizons detected: {horizons}" if horizons else "no horizons detected",
    )

    if schema_path.exists():
        schema = _load_json(schema_path)
        _check(
            schema.get("schema_hash") == schema_hash(),
            "schema hash matches feature builder",
        )

    for horizon in horizons:
        horizon_dir = root / f"horizon_h{horizon}"
        _check(horizon_dir.exists(), f"horizon folder exists: {horizon_dir}")
        for split in ("train", "val", "test"):
            found = list(horizon_dir.glob(f"{split}.*"))
            ok = len(found) == 1
            _check(ok, f"{split} file exists for h{horizon}")
            if ok:
                df = (
                    pd.read_parquet(found[0])
                    if found[0].suffix == ".parquet"
                    else pd.read_csv(found[0])
                )
                expected = set(
                    feature_names() + [f"y_up_h{horizon}", f"fut_ret_h{horizon}"]
                )
                _check(
                    expected.issubset(set(df.columns)),
                    f"{split} columns include features + labels for h{horizon}",
                )

    print(f"Summary: {checks} checks, {failures} FAIL")
    return 1 if failures else 0


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate ML datasets produced from scenarios."
    )
    parser.add_argument("--root", required=True)
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    return validate(Path(args.root))


if __name__ == "__main__":
    raise SystemExit(main())
