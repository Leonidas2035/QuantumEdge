import json
from pathlib import Path

from hermes.research.offline.scalper_bot.scenarios.build import (
    build_scenarios_pipeline,
)
from hermes.research.offline.scalper_bot.scenarios.validate import (
    validate_scenarios,
)


def _write_ticks_csv(path: Path, count: int = 3000) -> None:
    lines = ["timestamp,price,qty,side"]
    ts = 1700000000000
    price = 45000.0
    for i in range(count):
        price += 0.01
        side = "buy" if i % 2 == 0 else "sell"
        lines.append(f"{ts},{price:.2f},0.001,{side}")
        ts += 50
    path.write_text("\n".join(lines), encoding="utf-8")


def test_build_and_validate(tmp_path: Path) -> None:
    ticks_path = tmp_path / "ticks.csv"
    _write_ticks_csv(ticks_path, count=2500)
    out_root = tmp_path / "scenarios"
    code = build_scenarios_pipeline(
        symbol="BTCUSDT",
        ticks_path=ticks_path,
        depth_path=None,
        out_root=out_root,
        max_episodes=10,
        workers=1,
        limit_rows=None,
        output_format="csv",
    )
    assert code == 0
    root = out_root / "BTCUSDT"
    assert root.exists()
    assert validate_scenarios("BTCUSDT", root) == 0


def test_deterministic_manifest(tmp_path: Path) -> None:
    ticks_path = tmp_path / "ticks.csv"
    _write_ticks_csv(ticks_path, count=2000)
    out_root_1 = tmp_path / "run1"
    out_root_2 = tmp_path / "run2"
    build_scenarios_pipeline("BTCUSDT", ticks_path, None, out_root_1, 5, 1, None, "csv")
    build_scenarios_pipeline("BTCUSDT", ticks_path, None, out_root_2, 5, 1, None, "csv")

    manifest_1 = json.loads(
        (out_root_1 / "BTCUSDT" / "S00" / "manifest.json").read_text(encoding="utf-8")
    )
    manifest_2 = json.loads(
        (out_root_2 / "BTCUSDT" / "S00" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest_1.get("episodes") == manifest_2.get("episodes")


def test_validate_missing_files(tmp_path: Path) -> None:
    ticks_path = tmp_path / "ticks.csv"
    _write_ticks_csv(ticks_path, count=2000)
    out_root = tmp_path / "scenarios"
    build_scenarios_pipeline("BTCUSDT", ticks_path, None, out_root, 5, 1, None, "csv")
    schema_path = out_root / "BTCUSDT" / "S00" / "schema.json"
    schema_path.unlink()
    assert validate_scenarios("BTCUSDT", out_root / "BTCUSDT") == 1
