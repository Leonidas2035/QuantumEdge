"""Generate a markdown report for ML datasets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Optional


def _load_json(path: Path) -> Dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def render_report(root: Path) -> str:
    root = root.resolve()
    lines: List[str] = ["# ML Dataset Report", ""]
    config_path = root / "config_snapshot.json"
    if config_path.exists():
        config = _load_json(config_path)
        lines.append(f"Symbol: {config.get('symbol')}")
        label_cfg = config.get("label_config", {})
        lines.append(f"Horizons: {label_cfg.get('horizons')}")
        lines.append(f"Label threshold (bps): {label_cfg.get('label_thr_bps')}")
        lines.append("")

    for reminder in sorted(root.glob("horizon_h*")):
        stats_path = reminder / "stats.json"
        if not stats_path.exists():
            continue
        stats = _load_json(stats_path)
        lines.append(f"## {reminder.name}")
        lines.append("")
        lines.append(f"Rows: {stats.get('rows')}")
        lines.append(f"Class balance: {stats.get('class_balance')}")
        lines.append(f"Dropped: {stats.get('dropped')}")
        lines.append("")
    return "\n".join(lines) + "\n"


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate dataset report.")
    parser.add_argument("--root", required=True)
    parser.add_argument("--format", choices=["md"], default="md")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    root = Path(args.root)
    text = render_report(root)
    report_dir = root / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    out_path = report_dir / "dataset_report.md"
    out_path.write_text(text, encoding="utf-8")
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
