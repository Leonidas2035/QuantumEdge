from hermes.supervisor.security import (
    check_dashboard_auth,
    dashboard_auth_required,
)


def test_dashboard_auth_required():
    assert dashboard_auth_required("token") is True
    assert dashboard_auth_required("none") is False


def test_dashboard_auth_token():
    headers = {"Authorization": "Bearer secret"}
    assert check_dashboard_auth(headers, "token", "secret") is True
    assert check_dashboard_auth(headers, "token", "wrong") is False
    assert check_dashboard_auth({}, "none", "") is True
