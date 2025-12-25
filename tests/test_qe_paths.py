import os
from pathlib import Path

from tools.qe_paths import find_repo_root, get_paths


def test_find_repo_root_from_any_cwd(tmp_path: Path):
    cwd = Path.cwd()
    try:
        os.chdir(tmp_path)
        root = find_repo_root()
    finally:
        os.chdir(cwd)
    assert (root / "QuantumEdge.py").exists()


def test_get_paths_contains_artifacts():
    paths = get_paths()
    assert "artifacts_dir" in paths
    assert Path(paths["artifacts_dir"]).name == "artifacts"
