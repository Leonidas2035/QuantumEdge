import urllib.request

from quantum_edge_core.supervisor.supervisor.process_manager import (
    _http_health,
    _tcp_health,
)


class DummyResponse:
    def __init__(self, status=200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_http_healthcheck_ok(monkeypatch):
    def _fake_urlopen(req, timeout=1):
        return DummyResponse(status=200)

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    assert _http_health("http://127.0.0.1:8700/health", 1)


def test_tcp_healthcheck_ok(monkeypatch):
    class DummySocket:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def _fake_conn(addr, timeout=1):
        return DummySocket()

    monkeypatch.setattr(
        "supervisor.process_manager.socket.create_connection", _fake_conn
    )
    assert _tcp_health("127.0.0.1", 1234, 1)
