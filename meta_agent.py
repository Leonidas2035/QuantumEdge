import os
import runpy
import sys


def main() -> int:
    base = os.path.abspath(os.path.dirname(__file__))
    sys.path.insert(0, os.path.join(base, "src"))
    try:
        runpy.run_module(
            "quantum_edge_infra.automation.meta_agent.meta_agent",
            run_name="__main__",
            alter_sys=True,
        )
    except ImportError as e:
        print(f"[ERROR] Failed to run meta_agent module: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
