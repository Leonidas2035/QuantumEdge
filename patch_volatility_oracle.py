import re


def main():
    with open(
        "src/quantum_edge_core/ai_scalper_bot/bot/execution/volatility_oracle.py", "r"
    ) as f:
        content = f.read()

    new_method = """

    def calculate_atr(self) -> float:
        \"\"\"
        Calculates 7-day Trimmed Range Volatility (TRV).
        Removes the top and bottom 5% outliers of absolute returns to stabilize ATR.
        Returns the absolute price move estimate.
        \"\"\"
        history = list(self.price_history)
        if len(history) < 2:
            return 0.0

        # Calculate absolute price differences
        diffs = [abs(p2 - p1) for p1, p2 in zip(history[:-1], history[1:])]

        # Sort to trim outliers (Trimmed Range Volatility)
        diffs.sort()
        n = len(diffs)
        trim_idx = max(1, int(n * 0.05))

        trimmed_diffs = diffs[trim_idx:-trim_idx] if len(diffs) > 2 * trim_idx else diffs

        if not trimmed_diffs:
            return 0.0

        return sum(trimmed_diffs) / len(trimmed_diffs)
"""

    if "def calculate_atr" not in content:
        content += new_method
        with open(
            "src/quantum_edge_core/ai_scalper_bot/bot/execution/volatility_oracle.py",
            "w",
        ) as f:
            f.write(content)


if __name__ == "__main__":
    main()
