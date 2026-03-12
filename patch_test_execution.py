import re


def main():
    with open("src/quantum_edge_core/ai_scalper_bot/tests/test_execution.py", "r") as f:
        content = f.read()

    # Enable tests again
    content = content.replace(
        'pytestmark = pytest.mark.skip(reason="Tests designed for old AdaptiveGridStrategy")',
        "",
    )

    with open("src/quantum_edge_core/ai_scalper_bot/tests/test_execution.py", "w") as f:
        f.write(content)


if __name__ == "__main__":
    main()
