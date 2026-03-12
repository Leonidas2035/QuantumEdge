import re


def main():
    with open("src/quantum_edge_core/ai_scalper_bot/tests/test_execution.py", "r") as f:
        content = f.read()

    # Add import time
    content = "import time\n" + content

    # Add @pytest.mark.asyncio and import asyncio to test_position_sell_reduction
    # and actually make it an async test if it needs an event loop for telemetry
    content = content.replace(
        "def test_position_sell_reduction():",
        "@pytest.mark.asyncio\nasync def test_position_sell_reduction():",
    )

    with open("src/quantum_edge_core/ai_scalper_bot/tests/test_execution.py", "w") as f:
        f.write(content)


if __name__ == "__main__":
    main()
