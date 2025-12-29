from pathlib import Path

from supervisor.security import is_path_allowed


def test_policy_path_allowlist(tmp_path: Path):
    base = tmp_path / "artifacts"
    base.mkdir()
    allowed = base / "policy.json"
    allowed.write_text("{}", encoding="utf-8")
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")

    assert is_path_allowed(allowed, base) is True
    assert is_path_allowed(outside, base) is False
