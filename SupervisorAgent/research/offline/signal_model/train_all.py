import argparse
import os
from pathlib import Path
from typing import List, Dict

from .train import train_model


def parse_args():
    parser = argparse.ArgumentParser(description="Train all signal models across symbols/horizons.")
    parser.add_argument(
        "--symbols",
        type=str,
        default="BTCUSDT",
        help="Comma-separated symbols to train (e.g., BTCUSDT,ETHUSDT)",
    )
    parser.add_argument(
        "--horizons",
        type=str,
        default="1,5,30",
        help="Comma-separated horizons (e.g., 1,5,30)",
    )
    parser.add_argument("--min-rows", type=int, default=1000, help="Minimum rows required to train per horizon.")
    parser.add_argument("--data", type=str, default=None, help="Path to tick CSV directory (defaults to data/ticks).")
    parser.add_argument("--limit-files", type=int, default=None, help="Limit number of tick CSVs to load (optional).")
    return parser.parse_args()


def main():
    args = parse_args()
    root = Path(os.getenv("QE_ROOT") or Path(__file__).resolve().parents[4])
    model_dir = root / "storage" / "models"
    dataset_dir = root / "storage" / "datasets"
    model_dir.mkdir(parents=True, exist_ok=True)
    dataset_dir.mkdir(parents=True, exist_ok=True)
    data_path = Path(args.data) if args.data else None

    symbols: List[str] = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    horizons: List[int] = [int(h.strip()) for h in args.horizons.split(",") if h.strip()]

    summary: Dict[str, Dict[int, Dict]] = {}
    for sym in symbols:
        for h in horizons:
            ok, info = train_model(
                symbol=sym,
                horizon=h,
                min_rows=args.min_rows,
                model_dir=model_dir,
                dataset_dir=dataset_dir,
                data_dir=data_path,
                limit_files=args.limit_files,
            )
            summary.setdefault(sym, {})[h] = {"status": "ok" if ok else "error", **info}

    print("\n=== TRAINING SUMMARY ===")
    for sym, horizons_data in summary.items():
        print(f"Symbol: {sym}")
        for h, info in horizons_data.items():
            status = info.get("status", "error")
            if status == "ok":
                metrics = info.get("metrics") or {}
                rows = info.get("rows")
                balance = info.get("class_balance")
                print(
                    f"  h={h}: OK rows={rows} balance={balance} "
                    f"acc={metrics.get('accuracy')} mcc={metrics.get('mcc')}"
                )
            else:
                print(f"  h={h}: ERROR {info.get('error')}")


if __name__ == "__main__":
    main()
