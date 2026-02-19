from datetime import datetime, timezone
from pathlib import Path

from supervisor.config import PathsConfig, SupervisorConfig
from supervisor.process_manager import ProcessInfo, ProcessManager
from supervisor.process_spec import ProcessSpec


class DummyPopen:
    def __init__(self, *args, **kwargs):
        self.pid = 4321
        self._running = True
        self.returncode = None

    def poll(self):
        return None if self._running else self.returncode

    def terminate(self):
        self._running = False
        self.returncode = 0

    def wait(self, timeout=None):
        return self.returncode


def _build_paths(tmp_path: Path) -> PathsConfig:
    return PathsConfig(
        qe_root=tmp_path,
        quantumedge_root=tmp_path,
        python_executable=Path("python"),
        meta_agent_root=tmp_path,
        logs_dir=tmp_path / "logs",
        runtime_dir=tmp_path / "runtime",
        events_dir=tmp_path / "logs" / "events",
        reports_dir=tmp_path / "reports",
    )


def _build_config() -> SupervisorConfig:
    return SupervisorConfig(
        mode="paper",
        heartbeat_port=8765,
        heartbeat_timeout_s=5,
        restart_max_attempts=3,
        restart_backoff_s=1,
    )


def test_idempotent_start(monkeypatch, tmp_path: Path) -> None:
    spec = ProcessSpec(
        name="bot", enabled=True, cwd=tmp_path, cmd=["python", "-c", "print('x')"]
    )
    manager = ProcessManager(
        paths=_build_paths(tmp_path),
        config=_build_config(),
        state_dir=tmp_path / "state",
        processes={"bot": spec},
        run_id="run123",
    )
    popen_calls = {"count": 0}

    def _fake_popen(*args, **kwargs):
        popen_calls["count"] += 1
        return DummyPopen()

    monkeypatch.setattr("supervisor.process_manager.subprocess.Popen", _fake_popen)

    info = manager.start("bot")
    assert info.pid == 4321
    assert popen_calls["count"] == 1

    runtime = manager._runtime["bot"]
    runtime.info = ProcessInfo(
        pid=4321,
        start_time=datetime.now(timezone.utc),
        last_exit_code=None,
        last_exit_time=None,
    )

    info = manager.start("bot")
    assert info.pid == 4321
    assert popen_calls["count"] == 1
