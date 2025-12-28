"""Scenario dataset build pipeline."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from .cutter import CutterConfig, Episode, build_manifest, build_schema_payload, build_stats, cut_scenarios
from .io import Tick, attach_depth, load_depth_snapshots, load_ticks
from .specs import build_scenarios


def build_scenarios_pipeline(
    symbol: str,
    ticks_path: Path,
    depth_path: Optional[Path],
    out_root: Path,
    max_episodes: int,
    workers: int,
    limit_rows: Optional[int],
    output_format: Optional[str],
) -> int:
    _ = workers
    config = _load_config()
    defaults = config.get("defaults", {})
    thresholds = _select_thresholds(symbol, config)

    ticks = load_ticks(ticks_path, symbol=symbol, limit=limit_rows)
    if not ticks:
        raise SystemExit(f"No ticks loaded from {ticks_path}")

    depth_snapshots = []
    if depth_path:
        depth_snapshots = load_depth_snapshots(depth_path, symbol=symbol)
        attach_depth(ticks, depth_snapshots, max_age_ms=int(defaults.get("depth_max_age_ms", 3000)))
    depth_available = bool(depth_snapshots)

    cutter_cfg = CutterConfig(
        window_ticks=int(defaults.get("window_ticks", 2000)),
        pre_roll_ticks=int(defaults.get("pre_roll_ticks", 300)),
        post_roll_ticks=int(defaults.get("post_roll_ticks", 300)),
        min_event_ticks=int(defaults.get("min_event_ticks", 500)),
        warmup_ticks=int(defaults.get("warmup_ticks", 300)),
        stride_ticks=int(defaults.get("stride_ticks", 200)),
        max_episodes_per_scenario=int(defaults.get("max_episodes_per_scenario", 50)),
        output_format=str(output_format or defaults.get("output_format", "csv")),
    )

    episodes_by_scenario = cut_scenarios(ticks, cutter_cfg, thresholds, max_total_episodes=max_episodes)
    scenarios = {spec.scenario_id: spec for spec in build_scenarios(thresholds)}

    symbol_root = out_root / symbol
    symbol_root.mkdir(parents=True, exist_ok=True)
    schema_payload = build_schema_payload()
    label_horizons = defaults.get("label_horizons", [1, 5, 15])
    for scenario_id, episodes in episodes_by_scenario.items():
        spec = scenarios[scenario_id]
        scenario_dir = symbol_root / scenario_id
        episodes_dir = scenario_dir / "episodes"
        episodes_dir.mkdir(parents=True, exist_ok=True)

        fmt = cutter_cfg.output_format.lower()
        for ep in episodes:
            out_path = episodes_dir / f"{ep.episode_id}.{fmt}"
            _write_episode(out_path, ep.ticks, fmt)

        manifest = build_manifest(symbol, spec, episodes, cutter_cfg, label_horizons, depth_available)
        stats = build_stats(episodes)
        (scenario_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        (scenario_dir / "stats.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
        (scenario_dir / "schema.json").write_text(json.dumps(schema_payload, indent=2), encoding="utf-8")
        (scenario_dir / "README.md").write_text(_render_readme(spec, manifest), encoding="utf-8")

    _write_splits(symbol_root, episodes_by_scenario, defaults)
    _write_summary(symbol_root, episodes_by_scenario, thresholds)
    return 0


def _write_episode(path: Path, ticks: List[Tick], fmt: str) -> None:
    fmt = fmt.lower()
    if fmt == "parquet":
        try:
            import pandas as pd

            df = _ticks_to_frame(ticks)
            df.to_parquet(path, index=False)
            return
        except Exception:
            fmt = "csv"
    if fmt == "csv":
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["ts_ms", "price", "qty", "side", "bid", "ask", "depth_usd"])
            for t in ticks:
                writer.writerow([t.ts_ms, t.price, t.qty, t.side or "", t.bid, t.ask, t.depth_usd])


def _ticks_to_frame(ticks: List[Tick]):
    import pandas as pd

    return pd.DataFrame(
        {
            "ts_ms": [t.ts_ms for t in ticks],
            "price": [t.price for t in ticks],
            "qty": [t.qty for t in ticks],
            "side": [t.side for t in ticks],
            "bid": [t.bid for t in ticks],
            "ask": [t.ask for t in ticks],
            "depth_usd": [t.depth_usd for t in ticks],
        }
    )


def _write_splits(root: Path, episodes_by_scenario: Dict[str, List[Episode]], defaults: Dict[str, object]) -> None:
    splits_dir = root / "splits"
    splits_dir.mkdir(parents=True, exist_ok=True)
    all_eps = []
    for scenario_id, episodes in episodes_by_scenario.items():
        for ep in episodes:
            all_eps.append(
                {
                    "scenario_id": scenario_id,
                    "episode_id": ep.episode_id,
                    "path": f"{scenario_id}/episodes/{ep.episode_id}.{defaults.get('output_format', 'csv')}",
                    "start_ts_ms": ep.ticks[0].ts_ms,
                    "end_ts_ms": ep.ticks[-1].ts_ms,
                }
            )
    all_eps = sorted(all_eps, key=lambda e: e["start_ts_ms"])
    train_pct = float(defaults.get("split_train_pct", 0.7))
    val_pct = float(defaults.get("split_val_pct", 0.15))
    total = len(all_eps)
    train_end = int(total * train_pct)
    val_end = train_end + int(total * val_pct)
    split_time = {
        "train": all_eps[:train_end],
        "val": all_eps[train_end:val_end],
        "test": all_eps[val_end:],
    }
    (splits_dir / "split_time.json").write_text(json.dumps(split_time, indent=2), encoding="utf-8")

    holdout = set(defaults.get("holdout_scenarios", []))
    split_holdout = {
        "train": [ep for ep in all_eps if ep["scenario_id"] not in holdout],
        "test": [ep for ep in all_eps if ep["scenario_id"] in holdout],
        "holdout_scenarios": sorted(list(holdout)),
    }
    (splits_dir / "split_scenario_holdout.json").write_text(json.dumps(split_holdout, indent=2), encoding="utf-8")


def _write_summary(root: Path, episodes_by_scenario: Dict[str, List[Episode]], thresholds: Dict[str, object]) -> None:
    total_eps = sum(len(eps) for eps in episodes_by_scenario.values())
    summary = {
        "total_episodes": total_eps,
        "scenarios": {sid: len(eps) for sid, eps in episodes_by_scenario.items()},
        "thresholds": thresholds,
    }
    (root / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")


def _render_readme(spec, manifest: Dict[str, object]) -> str:
    lines = [
        f"# {spec.scenario_id} — {spec.name}",
        "",
        f"Intent: {spec.intent}",
        "",
        f"Constraints: {spec.constraints}",
        "",
        f"Episodes: {len(manifest.get('episodes', []))}",
    ]
    if manifest.get("skipped"):
        lines.append(f"Skipped: {manifest.get('skip_reason')}")
    return "\n".join(lines) + "\n"


def _load_config() -> Dict[str, object]:
    cfg_path = Path(__file__).resolve().parents[2] / "config" / "scenarios.yaml"
    return yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}


def _select_thresholds(symbol: str, config: Dict[str, object]) -> Dict[str, object]:
    thresholds = dict((config.get("thresholds") or {}).get("default", {}))
    symbol_classes = config.get("symbol_classes") or {}
    for class_name, symbols in symbol_classes.items():
        if symbol in symbols:
            overrides = (config.get("thresholds") or {}).get(class_name, {})
            thresholds.update(overrides)
    return thresholds


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build scenario datasets (S00-S24).")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--ticks", required=True, help="Ticks file or directory.")
    parser.add_argument("--depth", default=None, help="Depth snapshots file or directory.")
    parser.add_argument("--out", required=True, help="Output root (data/scenarios/<SYMBOL>).")
    parser.add_argument("--episodes", type=int, default=200)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit-rows", type=int, default=None)
    parser.add_argument("--format", dest="output_format", default=None, help="csv|parquet")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    return build_scenarios_pipeline(
        symbol=args.symbol,
        ticks_path=Path(args.ticks),
        depth_path=Path(args.depth) if args.depth else None,
        out_root=Path(args.out),
        max_episodes=args.episodes,
        workers=args.workers,
        limit_rows=args.limit_rows,
        output_format=args.output_format,
    )


if __name__ == "__main__":
    raise SystemExit(main())
