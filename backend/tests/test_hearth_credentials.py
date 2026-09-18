from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from app.api import hearth as hearth_api
from app.schemas.hearth import HearthCredentialConnectRequest
from app.services import kubernetes_service


class _FakeOAuthResponse:
    def __init__(self, status_code, *, headers=None, payload=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._payload = payload or {}

    def json(self):
        return self._payload


class _FakeOAuthClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, url, **_kwargs):
        if url.endswith("/.well-known/oauth-authorization-server"):
            return _FakeOAuthResponse(
                200,
                payload={"issuer": "https://oauth.example.test"},
            )
        if "/oauth/authorize" in url:
            return _FakeOAuthResponse(
                302,
                headers={
                    "location": (
                        "https://oauth.example.test/callback/"
                        "#access_token=specific-user-token"
                    )
                },
            )
        raise AssertionError(f"Unexpected request: {url}")


@pytest.mark.asyncio
async def test_specific_user_login_saves_token_without_password(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        kubernetes_service.httpx,
        "AsyncClient",
        lambda **_kwargs: _FakeOAuthClient(),
    )

    async def fail_if_service_account_is_created(*_args, **_kwargs):
        raise AssertionError("specific-user login must not create a service account")

    monkeypatch.setattr(
        kubernetes_service.KubernetesService,
        "create_service_account_token",
        fail_if_service_account_is_created,
    )

    result = await kubernetes_service.KubernetesService.login_with_credentials(
        api_server="https://api.example.test:6443",
        username="hearth-reader",
        password="do-not-save-this",
        storage_path=str(tmp_path),
        cluster_name="hearth-management",
        use_service_account=False,
        allow_stored_password=False,
    )

    assert result["success"] is True
    assert result["auth_type"] == "oauth-token"
    assert result["username"] == "hearth-reader"

    saved_text = Path(result["kubeconfig_path"]).read_text()
    saved = yaml.safe_load(saved_text)

    saved_user = saved["users"][0]["user"]
    assert saved_user == {"token": "specific-user-token"}
    assert "do-not-save-this" not in saved_text


@pytest.mark.asyncio
async def test_hearth_credentials_route_preserves_supplied_user(
    monkeypatch, tmp_path
):
    captured = {}

    async def fake_login(**kwargs):
        captured.update(kwargs)
        return {"success": True, "auth_type": "oauth-token"}

    service = SimpleNamespace(
        reset=lambda: captured.setdefault("reset", True),
        get_status=lambda: SimpleNamespace(
            available=True,
            cluster_count=15,
            error=None,
        ),
    )
    monkeypatch.setattr(
        hearth_api.KubernetesService,
        "login_with_credentials",
        fake_login,
    )
    monkeypatch.setattr(hearth_api, "get_hearth_service", lambda: service)
    monkeypatch.setattr(
        hearth_api.settings,
        "KUBECONFIG_STORAGE_PATH",
        str(tmp_path),
    )

    response = await hearth_api.connect_hearth_with_credentials(
        HearthCredentialConnectRequest(
            api_server_url="https://api.example.test:6443",
            username="hearth-reader",
            password="secret",
        ),
        _user={"username": "control-center-admin"},
    )

    assert response.success is True
    assert "hearth-reader" in response.message
    assert captured["cluster_name"] == "hearth-management"
    assert captured["use_service_account"] is False
    assert captured["allow_stored_password"] is False
    assert captured["reset"] is True
