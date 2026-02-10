from pathlib import Path

from tools.qe_config import get_qe_config, get_qe_paths


def test_qe_paths_defaults():
    paths = get_qe_paths()
    assert "qe_root" in paths
    assert "config_dir" in paths
    assert Path(paths["config_dir"]).name == "config"
    # In src-layout, config is deeper
    assert Path(paths["qe_root"]) in Path(paths["config_dir"]).parents


def test_qe_config_supervisor_defaults():
    cfg = get_qe_config()
    supervisor = cfg["supervisor"]
    assert supervisor["host"]
    assert supervisor["port"] > 0
    assert supervisor["url"].startswith("http://")
