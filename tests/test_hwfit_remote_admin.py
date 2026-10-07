"""Remote hwfit probes SSH with the server's keys and must be admin-only."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from routes.hwfit_routes import setup_hwfit_routes


class _AuthManager:
    def __init__(self, *, configured=True, admins=()):
        self.is_configured = configured
        self._admins = set(admins)

    def is_admin(self, username):
        return username in self._admins


def _request(*, user=None, admins=(), configured=True, auth_manager=True):
    manager = _AuthManager(configured=configured, admins=admins) if auth_manager else None
    return SimpleNamespace(
        headers={},
        state=SimpleNamespace(current_user=user),
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=manager)),
    )


def _endpoint(path: str):
    router = setup_hwfit_routes()
    for route in router.routes:
        if getattr(route, "path", "") == path:
            return route.endpoint
    raise AssertionError(f"{path} route not found")


@pytest.mark.parametrize(
    "path,kwargs",
    [
        ("/api/hwfit/system", {}),
        ("/api/hwfit/models", {"limit": 1}),
        ("/api/hwfit/profiles", {"model": "demo"}),
        ("/api/hwfit/image-models", {}),
    ],
)
def test_remote_hwfit_probe_requires_admin(path, kwargs, monkeypatch):
    def _boom(*_args, **_kwargs):
        raise AssertionError("remote hardware probe must not run")

    monkeypatch.setattr("services.hwfit.hardware.detect_system", _boom)
    endpoint = _endpoint(path)

    with pytest.raises(HTTPException) as missing:
        endpoint(host="gpu.example", ssh_port="22", **kwargs)
    assert missing.value.status_code == 403

    with pytest.raises(HTTPException) as anonymous:
        endpoint(
            host="gpu.example",
            ssh_port="22",
            request=_request(user=None),
            **kwargs,
        )
    assert anonymous.value.status_code == 403

    with pytest.raises(HTTPException) as member:
        endpoint(
            host="alice@gpu.example",
            ssh_port="22",
            request=_request(user="alice"),
            **kwargs,
        )
    assert member.value.status_code == 403

    with pytest.raises(HTTPException) as token:
        endpoint(
            host="gpu.example",
            ssh_port="22",
            request=_request(user="api", admins={"admin"}),
            **kwargs,
        )
    assert token.value.status_code == 403


def test_admin_remote_probe_is_allowed_without_starting_ssh(monkeypatch):
    calls = []

    def _stub(host="", ssh_port="", platform="", fresh=False):
        calls.append((host, ssh_port, platform, fresh))
        return {"error": "stubbed"}

    monkeypatch.setattr("services.hwfit.hardware.detect_system", _stub)
    endpoint = _endpoint("/api/hwfit/system")
    result = endpoint(
        host="gpu.example",
        ssh_port="22",
        request=_request(user="admin", admins={"admin"}),
    )

    assert result == {"error": "stubbed"}
    assert calls == [("gpu.example", "22", "", False)]


def test_auth_disabled_single_user_can_probe_a_remote_host(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")

    def _stub(host="", ssh_port="", platform="", fresh=False):
        return {"host": host, "error": "stubbed"}

    monkeypatch.setattr("services.hwfit.hardware.detect_system", _stub)
    endpoint = _endpoint("/api/hwfit/system")
    result = endpoint(
        host="gpu.example",
        request=_request(user=None, auth_manager=False),
    )

    assert result["host"] == "gpu.example"
    assert result["error"] == "stubbed"


def test_local_hwfit_probe_stays_available_without_admin(monkeypatch):
    def _stub(host="", ssh_port="", platform="", fresh=False):
        assert host == ""
        return {"host": "", "local": True}

    monkeypatch.setattr("services.hwfit.hardware.detect_system", _stub)
    endpoint = _endpoint("/api/hwfit/system")
    result = endpoint(request=_request(user="alice"))

    assert result == {"host": "", "local": True}
