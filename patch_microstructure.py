import re


def main():
    with open(
        "src/quantum_edge_core/market_data/analytics/microstructure.py", "r"
    ) as f:
        content = f.read()

    new_method = """

    def detect_liquidity_walls(
        self, order_book: dict, avg_volume_multiplier: float = 5.0
    ) -> list[dict]:
        \"\"\"
        Detect Level-2 Liquidity Walls (Blocks).
        Identifies price levels where the volume exceeds average_volume * avg_volume_multiplier.
        Psychological round numbers (modulo 100 or 1000) have increased priority/weight.
        \"\"\"
        walls = []
        bids = order_book.get("bids", [])
        asks = order_book.get("asks", [])

        all_levels = bids + asks
        if not all_levels:
            return walls

        total_volume = sum(level[1] for level in all_levels)
        avg_volume = total_volume / len(all_levels)

        threshold = avg_volume * avg_volume_multiplier

        def process_side(levels, side):
            for price, qty in levels:
                is_round = (price % 100 == 0)
                # If round number, lower the threshold requirement by 20%
                adjusted_threshold = threshold * 0.8 if is_round else threshold

                if qty >= adjusted_threshold:
                    walls.append({
                        "price": float(price),
                        "qty": float(qty),
                        "side": side,
                        "is_round": is_round,
                    })

        process_side(bids, "BID")
        process_side(asks, "ASK")

        return walls
"""

    if "def detect_liquidity_walls" not in content:
        content = content + new_method
    else:
        # replace the existing one
        content = re.sub(
            r"    def detect_liquidity_walls.*?return walls\n",
            new_method,
            content,
            flags=re.DOTALL,
        )

    with open(
        "src/quantum_edge_core/market_data/analytics/microstructure.py", "w"
    ) as f:
        f.write(content)


if __name__ == "__main__":
    main()
