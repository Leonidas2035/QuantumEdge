"""Scenario dataset report generator."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import List, Optional

from .build import _load_config, _select_thresholds
from .specs import build_scenarios


def build_report(symbol: str, root: Path, fmt: str = "md") -> Path:
    config = _load_config()
    thresholds = _select_thresholds(symbol, config)
    specs = build_scenarios(thresholds)
    rows = []
    for spec in specs:
        scenario_dir = root / spec.scenario_id
        manifest_path = scenario_dir / "manifest.json"
        stats_path = scenario_dir / "stats.json"
        if not manifest_path.exists() or not stats_path.exists():
            rows.append((spec.scenario_id, "MISSING", 0, None))
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            stats = json.loads(stats_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            rows.append((spec.scenario_id, "INVALID_JSON", 0, None))
            continue
        episodes = len(manifest.get("episodes", []))
        skip = manifest.get("skipped")
        status = "SKIP" if skip else "OK"
        vol = (stats.get("metrics", {}) or {}).get("vol_bps", {}).get("mean")
        rows.append((spec.scenario_id, status, episodes, vol))

    report_path = root / f"report.{fmt}"
    if fmt == "md":
        report_path.write_text(_render_md(symbol, rows), encoding="utf-8")
    else:
        report_path.write_text(
            json.dumps({"symbol": symbol, "rows": rows}, indent=2), encoding="utf-8"
        )
    return report_path


def _render_md(symbol: str, rows) -> str:
    lines = [
        f"# Scenario Report — {symbol}",
        "",
        "| Scenario | Status | Episodes | Vol (mean bps) |",
        "| --- | --- | --- | --- |",
    ]
    for scenario_id, status, episodes, vol in rows:
        vol_str = f"{vol:.3f}" if isinstance(vol, (float, int)) else "n/a"
        lines.append(f"| {scenario_id} | {status} | {episodes} | {vol_str} |")
    return "\n".join(lines) + "\n"


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scenario report.")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--format", default="md", choices=["md", "json"])
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    report_path = build_report(args.symbol, Path(args.root), fmt=args.format)
    print(f"Report written: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
