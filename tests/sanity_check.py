import sys
from pathlib import Path

# Add src to sys.path for testing purposes (simulating PYTHONPATH=src)
src_path = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(src_path))


def test_imports():
    print("Testing imports...")
    try:
        import quantum_edge_core

        print(f"SUCCESS: Imported quantum_edge_core from {quantum_edge_core.__file__}")
    except ImportError as e:
        print(f"FAILURE: Could not import quantum_edge_core: {e}")
        return False

    try:
        import quantum_edge_ml

        print(f"SUCCESS: Imported quantum_edge_ml from {quantum_edge_ml.__path__}")
    except ImportError as e:
        print(f"FAILURE: Could not import quantum_edge_ml: {e}")
        return False

    try:
        import quantum_edge_infra

        print(f"SUCCESS: Imported quantum_edge_infra from {quantum_edge_infra.__path__}")
    except ImportError as e:
        print(f"FAILURE: Could not import quantum_edge_infra: {e}")
        return False

    try:
        from quantum_edge_infra.tools import qe_paths

        print(f"SUCCESS: Imported quantum_edge_infra.tools.qe_paths from {qe_paths.__file__}")
    except ImportError as e:
        print(f"FAILURE: Could not import quantum_edge_infra.tools.qe_paths: {e}")
        return False

    return True


if __name__ == "__main__":
    if test_imports():
        print("ALL TESTS PASSED")
        sys.exit(0)
    else:
        print("TESTS FAILED")
        sys.exit(1)
