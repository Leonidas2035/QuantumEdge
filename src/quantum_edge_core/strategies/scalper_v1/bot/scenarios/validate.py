"""Validation checks for scenario datasets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import List, Optional

from quantum_edge_core.strategies.scalper_v1.bot.ml.features import \
    builder as feature_builder

from .build import _load_config, _select_thresholds
from .specs import build_scenarios


def validate_scenarios(symbol: str, root: Path) -> int:
    config = _load_config()
    thresholds = _select_thresholds(symbol, config)
    specs = build_scenarios(thresholds)
    required_hash = feature_builder.schema_hash()
    failures = 0
    warnings = 0
    checks = 0

    for spec in specs:
        checks += 1
        scenario_dir = root / spec.scenario_id
        if not scenario_dir.exists():
            print(f"[FAIL] Missing scenario dir: {scenario_dir}")
            failures += 1
            continue
        manifest_path = scenario_dir / "manifest.json"
        stats_path = scenario_dir / "stats.json"
        schema_path = scenario_dir / "schema.json"
        if (
            not manifest_path.exists()
            or not stats_path.exists()
            or not schema_path.exists()
        ):
            print(f"[FAIL] Missing manifest/stats/schema for {spec.scenario_id}")
            failures += 1
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"[FAIL] Invalid manifest JSON for {spec.scenario_id}")
            failures += 1
            continue
        try:
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"[FAIL] Invalid schema JSON for {spec.scenario_id}")
            failures += 1
            continue

        if schema.get("feature_schema_hash") != required_hash:
            print(f"[FAIL] Schema hash mismatch for {spec.scenario_id}")
            failures += 1

        if manifest.get("skipped"):
            print(
                f"[WARN] Scenario {spec.scenario_id} skipped: {manifest.get('skip_reason')}"
            )
            warnings += 1
            continue

        episodes_dir = scenario_dir / "episodes"
        episodes = manifest.get("episodes", [])
        if not episodes:
            print(f"[FAIL] No episodes listed for {spec.scenario_id}")
            failures += 1
            continue
        missing_files = [
            ep for ep in episodes if not (scenario_dir / ep.get("file", "")).exists()
        ]
        if missing_files:
            print(f"[FAIL] Missing episode files for {spec.scenario_id}")
            failures += 1
        else:
            print(f"[PASS] {spec.scenario_id}: {len(episodes)} episodes")

    print(f"Summary: {checks} scenarios, {failures} FAIL, {warnings} WARN")
    return 1 if failures else 0


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate scenario datasets (S00-S24)."
    )
    parser.add_argument("--symbol", required=True)
    parser.add_argument(
        "--root", required=True, help="Scenario root (data/scenarios/<SYMBOL>)."
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    return validate_scenarios(args.symbol, Path(args.root))


if __name__ == "__main__":
    raise SystemExit(main())
