from pathlib import Path

import pytest

from tools.qe_config_loader import load_yaml, merge_defaults, validate_required


def test_load_yaml_missing(tmp_path: Path):
    missing = tmp_path / "missing.yaml"
    with pytest.raises(FileNotFoundError):
        load_yaml(missing)


def test_merge_defaults_nested():
    base = {"a": {"b": 1}, "c": 2}
    override = {"a": {"d": 3}}
    merged = merge_defaults(base, override)
    assert merged["a"]["b"] == 1
    assert merged["a"]["d"] == 3
    assert merged["c"] == 2


def test_validate_required_paths():
    data = {"a": {"b": 1}}
    validate_required(data, ["a.b"])
    with pytest.raises(ValueError):
        validate_required(data, ["a.c"])
