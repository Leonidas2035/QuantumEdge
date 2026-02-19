import textwrap
from pathlib import Path

import pytest
from supervisor.config_loader import load_processes_spec


def _write(path: Path, content: str) -> Path:
    path.write_text(textwrap.dedent(content), encoding="utf-8")
    return path


def test_process_spec_validation_ok(tmp_path: Path) -> None:
    (tmp_path / "hub").mkdir()
    spec = _write(
        tmp_path / "processes.yaml",
        """
        version: 1
        processes:
          hub:
            enabled: true
            cwd: "./hub"
            cmd: ["python", "-m", "hub"]
            healthcheck:
              type: "http"
              url: "http://127.0.0.1:8700/health"
        """,
    )
    specs = load_processes_spec(spec, tmp_path)
    assert "hub" in specs
    assert specs["hub"].cwd == (tmp_path / "hub").resolve()


def test_process_spec_validation_missing_cmd(tmp_path: Path) -> None:
    (tmp_path / "hub").mkdir()
    spec = _write(
        tmp_path / "processes.yaml",
        """
        version: 1
        processes:
          hub:
            enabled: true
            cwd: "./hub"
        """,
    )
    with pytest.raises(ValueError, match="cmd"):
        load_processes_spec(spec, tmp_path)


def test_process_spec_validation_bad_healthcheck(tmp_path: Path) -> None:
    (tmp_path / "hub").mkdir()
    spec = _write(
        tmp_path / "processes.yaml",
        """
        version: 1
        processes:
          hub:
            enabled: true
            cwd: "./hub"
            cmd: ["python", "-m", "hub"]
            healthcheck:
              type: "nope"
        """,
    )
    with pytest.raises(ValueError, match="healthcheck"):
        load_processes_spec(spec, tmp_path)


def test_process_spec_validation_missing_cwd(tmp_path: Path) -> None:
    spec = _write(
        tmp_path / "processes.yaml",
        """
        version: 1
        processes:
          hub:
            enabled: true
            cwd: "./missing"
            cmd: ["python", "-m", "hub"]
        """,
    )
    with pytest.raises(ValueError, match="cwd"):
        load_processes_spec(spec, tmp_path)
