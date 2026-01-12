from __future__ import annotations

from types import SimpleNamespace

import supervisor_llm.cli as cli
from supervisor_llm.contracts.decision_v1 import fallback_decision


def test_cli_routed_default(monkeypatch, tmp_path, capsys):
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text("risk check", encoding="utf-8")

    monkeypatch.setenv("SUPERVISOR_CLI_ROUTED_DEFAULT", "1")

    def fake_route(prompt, timeout_s, mode, teacher_ratio, force_teacher):
        decision = fallback_decision("ok")
        return SimpleNamespace(decision=decision, error=None)

    monkeypatch.setattr(cli, "_route_decision", fake_route)

    monkeypatch.setattr(
        cli.sys,
        "argv",
        ["supervisor-llm", "decision", "--prompt-file", str(prompt_path)],
    )
    exit_code = cli.main()
    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out.strip().startswith("{")
