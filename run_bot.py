import os
import runpy
import sys

def main() -> int:
    base = os.path.abspath(os.path.dirname(__file__))

    # Ensure src/ is in sys.path
    src_path = os.path.join(base, "src")
    if src_path not in sys.path:
        sys.path.insert(0, src_path)

    # Add module roots for convenience
    sys.path.append(os.path.join(src_path, "quantum_edge_core"))
    sys.path.append(os.path.join(src_path, "quantum_edge_infra"))
    sys.path.append(os.path.join(src_path, "quantum_edge_ml"))

    # Target path in new layout
    target = os.path.join(src_path, "quantum_edge_core", "strategies", "scalper_v1", "run_bot.py")
    if not os.path.exists(target):
        print(f"[ERROR] run_bot entrypoint not found: {target}")
        return 1

    # Also add the target's directory to path so it can find its siblings
    sys.path.insert(0, os.path.dirname(target))

    runpy.run_path(target, run_name="__main__")
    return 0

if __name__ == "__main__":
    sys.exit(main())
