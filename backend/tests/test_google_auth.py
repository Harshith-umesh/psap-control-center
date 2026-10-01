import asyncio
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from fastapi import Request
from jose import jwt

from app.api import auth as auth_api
from app.core import auth


def _request(*, cookie: str = "") -> Request:
    headers = [(b"x-forwarded-proto", b"https")]
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    return Request({
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": "/api/v1/auth/google/callback",
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 443),
    })


def test_google_session_preserves_verified_identity(monkeypatch):
    monkeypatch.setattr(auth.settings, "SECRET_KEY", "test-secret")
    token = auth.create_session_token(
        "person@example.com",
        "user",
        subject="google:12345",
        email="person@example.com",
        name="Example Person",
        auth_provider="google",
    )

    assert auth.decode_session_token(token) == {
        "subject": "google:12345",
        "username": "person@example.com",
        "email": "person@example.com",
        "name": "Example Person",
        "auth_provider": "google",
        "role": "user",
    }


def test_requester_annotations_are_server_identity_fields():
    annotations = auth.requester_annotations({
        "subject": "google:12345",
        "email": "person@example.com",
        "name": "Example Person",
        "auth_provider": "google",
    })

    assert annotations[auth.REQUESTER_SUBJECT_ANNOTATION] == "google:12345"
    assert annotations[auth.REQUESTER_EMAIL_ANNOTATION] == "person@example.com"
    assert annotations[auth.REQUESTER_NAME_ANNOTATION] == "Example Person"
    assert annotations[auth.REQUESTER_PROVIDER_ANNOTATION] == "google"


def test_google_login_uses_configured_redirect_and_domain(monkeypatch):
    monkeypatch.setattr(auth_api.settings, "GOOGLE_OAUTH_ENABLED", True)
    monkeypatch.setattr(auth_api.settings, "GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setattr(
        auth_api.settings, "GOOGLE_REDIRECT_URI", "https://dev.example.com"
    )
    monkeypatch.setattr(auth_api.settings, "GOOGLE_ALLOWED_DOMAIN", "example.com")
    monkeypatch.setattr(auth_api.settings, "SECRET_KEY", "test-secret")

    response = asyncio.run(auth_api.google_login(_request()))
    query = parse_qs(urlparse(response.headers["location"]).query)

    assert query["client_id"] == ["client-id"]
    assert query["redirect_uri"] == ["https://dev.example.com"]
    assert query["hd"] == ["example.com"]
    assert query["scope"] == ["openid email profile"]
    assert query["code_challenge_method"] == ["S256"]
    assert len(query["code_challenge"][0]) >= 43
    assert "google_oauth_state=" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "Secure" in response.headers["set-cookie"]


def test_google_id_token_uses_official_verifier(monkeypatch):
    monkeypatch.setattr(auth_api.settings, "GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setattr(auth_api.settings, "GOOGLE_ALLOWED_DOMAIN", "example.com")

    def fake_verify(token, _request, audience):
        assert token == "signed-id-token"
        assert audience == "client-id"
        return {
            "iss": "https://accounts.google.com",
            "aud": "client-id",
            "sub": "12345",
            "nonce": "expected-nonce",
            "email": "person@example.com",
            "email_verified": True,
            "hd": "example.com",
        }

    monkeypatch.setattr(auth_api.google_id_token, "verify_oauth2_token", fake_verify)

    claims = asyncio.run(
        auth_api._verify_google_id_token("signed-id-token", "expected-nonce")
    )

    assert claims["sub"] == "12345"
    assert claims["email"] == "person@example.com"


def test_google_callback_creates_user_session(monkeypatch):
    state = "expected-state"
    nonce = "expected-nonce"
    monkeypatch.setattr(auth_api.settings, "GOOGLE_OAUTH_ENABLED", True)
    monkeypatch.setattr(auth_api.settings, "GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setattr(auth_api.settings, "GOOGLE_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(
        auth_api.settings, "GOOGLE_REDIRECT_URI", "https://dev.example.com"
    )
    monkeypatch.setattr(auth_api.settings, "GOOGLE_ALLOWED_DOMAIN", "example.com")
    monkeypatch.setattr(auth_api.settings, "GOOGLE_ADMIN_EMAILS", "admin@example.com")
    monkeypatch.setattr(auth_api.settings, "SECRET_KEY", "test-secret")

    state_token = jwt.encode(
        {
            "state": state,
            "nonce": nonce,
            "code_verifier": "test-code-verifier",
            "type": "google_oauth",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        "test-secret",
        algorithm=auth_api.settings.ALGORITHM,
    )

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"id_token": "signed-id-token"}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, data):
            assert data["client_secret"] == "client-secret"
            assert data["redirect_uri"] == "https://dev.example.com"
            assert data["code_verifier"] == "test-code-verifier"
            return FakeResponse()

    async def fake_verify(id_token, expected_nonce):
        assert id_token == "signed-id-token"
        assert expected_nonce == nonce
        return {
            "sub": "12345",
            "email": "admin@example.com",
            "name": "Example Admin",
        }

    monkeypatch.setattr(auth_api.httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    monkeypatch.setattr(auth_api, "_verify_google_id_token", fake_verify)

    class FakeResult:
        @staticmethod
        def scalar_one_or_none():
            return None

    class FakeDb:
        def __init__(self):
            self.added = None

        async def execute(self, _query):
            return FakeResult()

        def add(self, user):
            self.added = user

        async def commit(self):
            return None

    db = FakeDb()

    request = _request(cookie=f"{auth_api.OAUTH_STATE_COOKIE}={state_token}")
    response = asyncio.run(
        auth_api.google_callback(
            request,
            auth_api.GoogleCallbackRequest(code="code", state=state),
            db,
        )
    )
    payload = json.loads(response.body)

    assert payload == {
        "username": "admin@example.com",
        "email": "admin@example.com",
        "name": "Example Admin",
        "auth_provider": "google",
        "role": "admin",
    }
    cookies = response.headers.getlist("set-cookie")
    assert any(cookie.startswith("session=") and "HttpOnly" in cookie for cookie in cookies)
    assert any(cookie.startswith(f"{auth_api.OAUTH_STATE_COOKIE}=") for cookie in cookies)
    assert db.added.email == "admin@example.com"
    assert db.added.is_admin is True
