from __future__ import annotations

import os
from pathlib import Path

import importlib.util
import pytest

SHELL_SCRIPTS = [
    "env_check.sh",
    "download_model.sh",
    "quantize_awq.sh",
    "build_engine.sh",
    "serve.sh",
    "bench.sh",
    "collect_versions.sh",
]
PY_SCRIPTS = [
    "prepare_calib.py",
    "smoke_local.py",
    "trtllm_flags.py",
]


def test_scripts_exist_and_executable():
    root = Path(__file__).resolve().parents[1]
    scripts_dir = root / "src" / "quantum_edge_ml" / "inference_engine" / "scripts"
    assert scripts_dir.is_dir()

    for script in SHELL_SCRIPTS:
        path = scripts_dir / script
        assert path.exists(), f"Missing script: {path}"
        if os.name == "posix":
            assert os.access(path, os.X_OK), f"Script not executable: {path}"

    for script in PY_SCRIPTS:
        path = scripts_dir / script
        assert path.exists(), f"Missing script: {path}"


@pytest.mark.parametrize(
    "help_text,expected",
    [
        ("--kv_cache_type {paged,auto}", ["--kv_cache_type", "paged"]),
        ("--paged_kv_cache enable", ["--paged_kv_cache", "enable"]),
        ("--no_kv_cache", []),
    ],
)
def test_detect_kv_cache_flags(help_text, expected):
    root = Path(__file__).resolve().parents[1]
    module_path = (
        root / "src" / "quantum_edge_ml" / "inference_engine" / "scripts" / "trtllm_flags.py"
    )
    spec = importlib.util.spec_from_file_location("trtllm_flags", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    detect_kv_cache_flags = module.detect_kv_cache_flags

    assert detect_kv_cache_flags(help_text) == expected


def test_engine_defaults_ngc_image_pinned():
    root = Path(__file__).resolve().parents[1]
    env_path = (
        root / "src" / "quantum_edge_ml" / "inference_engine" / "configs" / "engine_defaults.env"
    )
    content = env_path.read_text(encoding="utf-8")
    assert "NGC_IMAGE=" in content
    assert "<TAG>" not in content


def test_env_check_driver_warning_present():
    root = Path(__file__).resolve().parents[1]
    env_check = root / "src" / "quantum_edge_ml" / "inference_engine" / "scripts" / "env_check.sh"
    content = env_check.read_text(encoding="utf-8")
    assert "WARNING: Driver" in content
