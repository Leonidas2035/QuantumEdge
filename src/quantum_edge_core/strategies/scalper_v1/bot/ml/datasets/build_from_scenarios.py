"""Build ML datasets from scenario episodes (S00-S24)."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from quantum_edge_core.strategies.scalper_v1.bot.ml.features.builder import (
    build_feature_frame,
    feature_names,
    schema_hash,
    schema_version,
)
from quantum_edge_core.strategies.scalper_v1.bot.ml.labels.builder import (
    LabelConfig,
    build_labels,
    parse_horizons,
)
from quantum_edge_core.strategies.scalper_v1.bot.ml.datasets.io import (
    normalize_ticks,
    read_episode,
    write_frame,
)


def _load_splits(scenarios_root: Path) -> Dict[str, List[Dict[str, object]]]:
    split_path = scenarios_root / "splits" / "split_time.json"
    if not split_path.exists():
        raise FileNotFoundError(f"Missing split file: {split_path}")
    raw = json.loads(split_path.read_text(encoding="utf-8"))
    return {
        "train": list(raw.get("train", [])),
        "val": list(raw.get("val", [])),
        "test": list(raw.get("test", [])),
    }


def _schema_payload() -> Dict[str, object]:
    return {
        "schema_version": schema_version(),
        "schema_hash": schema_hash(),
        "feature_names": feature_names(),
    }


def build_from_scenarios(
    symbol: str,
    scenarios_root: Path,
    out_root: Path,
    horizons: List[int],
    label_mode: str,
    label_thr_bps: float,
    ignore_thr_bps: float,
    fee_bps: float,
    slippage_bps: float,
    output_format: str,
) -> int:
    symbol = symbol.upper()
    scenarios_root = scenarios_root.resolve()
    out_root = out_root.resolve()
    splits = _load_splits(scenarios_root)

    label_config = LabelConfig(
        horizons=tuple(horizons),
        label_mode=label_mode,
        label_thr_bps=label_thr_bps,
        ignore_thr_bps=ignore_thr_bps,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
    )

    out_root.mkdir(parents=True, exist_ok=True)
    reports_dir = out_root / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    config_snapshot = {
        "symbol": symbol,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "label_config": asdict(label_config),
        "output_format": output_format,
        "scenarios_root": str(scenarios_root),
        "schema_hash": schema_hash(),
    }
    (out_root / "config_snapshot.json").write_text(
        json.dumps(config_snapshot, indent=2), encoding="utf-8"
    )
    (out_root / "schema.json").write_text(
        json.dumps(_schema_payload(), indent=2), encoding="utf-8"
    )

    data_by_horizon: Dict[int, Dict[str, List[pd.DataFrame]]] = {
        int(h): {"train": [], "val": [], "test": []} for h in horizons
    }
    stats: Dict[int, Dict[str, object]] = {int(h): {} for h in horizons}
    scenario_counts: Dict[int, Dict[str, int]] = {int(h): {} for h in horizons}
    dropped: Dict[int, Dict[str, int]] = {
        int(h): {"features": 0, "labels": 0} for h in horizons
    }

    for split_name, episodes in splits.items():
        for ep in episodes:
            scenario_id = str(ep.get("scenario_id"))
            episode_id = str(ep.get("episode_id"))
            rel_path = Path(str(ep.get("path")))
            episode_path = scenarios_root / rel_path
            if not episode_path.exists():
                raise FileNotFoundError(f"Missing episode: {episode_path}")

            raw = read_episode(episode_path)
            ticks = normalize_ticks(raw)
            bars = build_feature_frame(ticks)
            if bars.empty:
                continue
            features = bars[feature_names()].copy()
            labels = build_labels(bars, label_config, price_col="price")
            valid_features = features.notna().all(axis=1)

            for horizon in horizons:
                cols = [f"fut_ret_h{horizon}", f"y_up_h{horizon}"]
                label_slice = labels[cols]
                valid_labels = label_slice.notna().all(axis=1)
                mask = valid_features & valid_labels

                dropped[int(horizon)]["features"] += int((~valid_features).sum())
                dropped[int(horizon)]["labels"] += int(
                    (valid_features & ~valid_labels).sum()
                )

                if not mask.any():
                    continue

                ts_ms = (bars.index.astype("int64") // 1_000_000).astype("int64")
                meta = pd.DataFrame(
                    {
                        "ts_ms": ts_ms,
                        "scenario_id": scenario_id,
                        "episode_id": episode_id,
                    },
                    index=bars.index,
                )
                combined = pd.concat([meta, features, label_slice], axis=1).loc[mask]

                data_by_horizon[int(horizon)][split_name].append(combined)
                scenario_counts[int(horizon)][scenario_id] = scenario_counts[
                    int(horizon)
                ].get(scenario_id, 0) + len(combined)

    for horizon in horizons:
        horizon_dir = out_root / f"horizon_h{horizon}"
        horizon_dir.mkdir(parents=True, exist_ok=True)

        horizon_stats = {
            "rows": {},
            "class_balance": {},
            "scenario_rows": scenario_counts[int(horizon)],
            "dropped": dropped[int(horizon)],
        }

        for split_name in ("train", "val", "test"):
            frames = data_by_horizon[int(horizon)][split_name]
            if frames:
                df = pd.concat(frames, ignore_index=True)
            else:
                df = pd.DataFrame(
                    columns=["ts_ms", "scenario_id", "episode_id"]
                    + feature_names()
                    + [f"fut_ret_h{horizon}", f"y_up_h{horizon}"]
                )
            out_path = horizon_dir / f"{split_name}.{output_format}"
            write_frame(out_path, df, output_format)
            horizon_stats["rows"][split_name] = len(df)

            y_col = f"y_up_h{horizon}"
            if y_col in df.columns and len(df):
                counts = df[y_col].value_counts(dropna=True).to_dict()
                horizon_stats["class_balance"][split_name] = {
                    str(k): int(v) for k, v in counts.items()
                }
            else:
                horizon_stats["class_balance"][split_name] = {}

        stats[int(horizon)] = horizon_stats
        (horizon_dir / "stats.json").write_text(
            json.dumps(horizon_stats, indent=2), encoding="utf-8"
        )

    report_path = reports_dir / "dataset_report.md"
    report_path.write_text(
        _render_report(symbol, horizons, stats, label_config), encoding="utf-8"
    )
    return 0


def _render_report(
    symbol: str,
    horizons: List[int],
    stats: Dict[int, Dict[str, object]],
    label_config: LabelConfig,
) -> str:
    lines = [f"# ML Dataset Report ({symbol})", ""]
    lines.append(f"Horizons: {', '.join(str(h) for h in horizons)}")
    lines.append(
        f"Label thresholds (bps): base={label_config.label_thr_bps}, ignore={label_config.ignore_thr_bps}"
    )
    lines.append("")
    for horizon in horizons:
        horizon_stats = stats.get(int(horizon), {})
        lines.append(f"## Horizon h{horizon}")
        lines.append("")
        rows = horizon_stats.get("rows", {})
        lines.append(
            f"Rows: train={rows.get('train', 0)} val={rows.get('val', 0)} test={rows.get('test', 0)}"
        )
        balance = horizon_stats.get("class_balance", {})
        if balance:
            lines.append(f"Class balance: {balance}")
        dropped = horizon_stats.get("dropped", {})
        if dropped:
            lines.append(
                f"Dropped: features={dropped.get('features', 0)} labels={dropped.get('labels', 0)}"
            )
        lines.append("")
    return "\n".join(lines) + "\n"


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build ML datasets from scenario episodes."
    )
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--scenarios-root", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--horizons", nargs="+", default=["1", "5", "30"])
    parser.add_argument("--mode", default="seconds", choices=["seconds", "ticks"])
    parser.add_argument("--label-thr-bps", type=float, default=2.0)
    parser.add_argument("--ignore-thr-bps", type=float, default=0.0)
    parser.add_argument("--fee-bps", type=float, default=0.0)
    parser.add_argument("--slippage-bps", type=float, default=0.0)
    parser.add_argument("--format", dest="output_format", default="csv")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    horizons = list(parse_horizons(args.horizons))
    return build_from_scenarios(
        symbol=args.symbol,
        scenarios_root=Path(args.scenarios_root),
        out_root=Path(args.out),
        horizons=horizons,
        label_mode=args.mode,
        label_thr_bps=args.label_thr_bps,
        ignore_thr_bps=args.ignore_thr_bps,
        fee_bps=args.fee_bps,
        slippage_bps=args.slippage_bps,
        output_format=args.output_format,
    )


if __name__ == "__main__":
    raise SystemExit(main())
