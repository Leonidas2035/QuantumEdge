import numpy as np
import pandas as pd
from pathlib import Path

OUTPUT_DIR = Path("data") / "ticks"
DEFAULT_FILE = OUTPUT_DIR / "BTCUSDT_synthetic.csv"


def generate_ticks(n: int = 5000, symbol: str = "BTCUSDT") -> pd.DataFrame:
    rng = np.random.default_rng(seed=42)
    base_price = 45000.0
    price_changes = rng.normal(loc=0, scale=5, size=n)
    prices = base_price + np.cumsum(price_changes)

    qty = rng.uniform(0.001, 0.01, size=n)
    sides = rng.choice(["buy", "sell"], size=n)

    timestamps = np.arange(0, n, dtype=int) * 50
    timestamps = timestamps + 1700000000000

    df = pd.DataFrame(
        {
            "timestamp": timestamps,
            "price": prices,
            "qty": qty,
            "side": sides,
        }
    )
    return df


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    df = generate_ticks()
    df.to_csv(DEFAULT_FILE, index=False)
    print(f"[OK] Generated {len(df)} synthetic ticks at {DEFAULT_FILE}")


if __name__ == "__main__":
    main()
