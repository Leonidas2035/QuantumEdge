"""CLI helpers for SupervisorAgent ModelOps."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import List, Optional

from SupervisorAgent.mlops.dataset_builder import build_dataset
from SupervisorAgent.mlops.trainer import train_horizons
from SupervisorAgent.mlops.validator import validate_model
from SupervisorAgent.mlops.publisher import publish_model


def _parse_horizons(raw: str) -> List[int]:
    return [int(h.strip()) for h in raw.split(",") if h.strip()]


def parse_ml_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="supervisor.py ml", description="ModelOps commands")
    sub = parser.add_subparsers(dest="ml_command", required=True)

    dataset = sub.add_parser("dataset", help="Build dataset")
    dataset.add_argument("--symbol", required=True)
    dataset.add_argument("--source", default="ticks", choices=["ticks", "bars"])
    dataset.add_argument("--input-dir", default=None)
    dataset.add_argument("--out-dir", default="artifacts/datasets")
    dataset.add_argument("--limit-rows", type=int, default=None)
    dataset.add_argument("--horizon", type=int, default=1)

    train = sub.add_parser("train", help="Train model(s)")
    train.add_argument("--symbol", required=True)
    train.add_argument("--horizons", default="1,5,30")
    train.add_argument("--source", default="ticks", choices=["ticks", "bars"])
    train.add_argument("--input-dir", default=None)
    train.add_argument("--artifacts-dir", default="artifacts")
    train.add_argument("--min-rows", type=int, default=1000)
    train.add_argument("--threshold", type=float, default=0.55)
    train.add_argument("--publish", action="store_true")

    validate = sub.add_parser("validate", help="Validate model against dataset")
    validate.add_argument("--manifest", required=True)
    validate.add_argument("--dataset", required=True)

    publish = sub.add_parser("publish", help="Publish artifact into runtime models")
    publish.add_argument("--artifact-dir", required=True)
    publish.add_argument("--runtime-dir", default="runtime")

    return parser.parse_args(argv)


def run_ml_command(args: argparse.Namespace) -> int:
    if args.ml_command == "dataset":
        info = build_dataset(
            symbol=args.symbol,
            source=args.source,
            input_dir=Path(args.input_dir) if args.input_dir else None,
            out_dir=Path(args.out_dir),
            limit_rows=args.limit_rows,
            horizon=args.horizon,
        )
        print(f"[OK] Dataset saved to {info['dataset_path']}")
        return 0

    if args.ml_command == "train":
        manifests = train_horizons(
            symbol=args.symbol,
            horizons=_parse_horizons(args.horizons),
            source=args.source,
            input_dir=Path(args.input_dir) if args.input_dir else None,
            artifacts_root=Path(args.artifacts_dir),
            min_rows=args.min_rows,
            thresholds={"p_up": args.threshold},
        )
        for path in manifests:
            print(f"[OK] Manifest: {path}")
            if args.publish:
                current_dir = publish_model(path.parent, Path("runtime"))
                print(f"[OK] Published to {current_dir}")
        return 0

    if args.ml_command == "validate":
        metrics = validate_model(Path(args.manifest), Path(args.dataset))
        print(f"[OK] Validation metrics: {metrics}")
        return 0

    if args.ml_command == "publish":
        current_dir = publish_model(Path(args.artifact_dir), Path(args.runtime_dir))
        print(f"[OK] Published to {current_dir}")
        return 0

    raise SystemExit("Unknown ml command")

