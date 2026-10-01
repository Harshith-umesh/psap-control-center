import asyncio
from datetime import datetime

import pytest
from fastapi import HTTPException, Request

from app.api import settings as settings_api
from app.core import auth
from app.models.user import User


def _request_with_session(token: str) -> Request:
    return Request({
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": "/api/v1/auth/me",
        "query_string": b"",
        "headers": [(b"cookie", f"session={token}".encode())],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 443),
    })


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _FakeDb:
    def __init__(self, value):
        self.value = value
        self.committed = False

    async def execute(self, _query):
        return _ScalarResult(self.value)

    async def commit(self):
        self.committed = True

    async def refresh(self, _value):
        return None


def _managed_user(*, is_admin: bool = False) -> User:
    now = datetime.utcnow()
    return User(
        id="user-id",
        username="person@example.com",
        email="person@example.com",
        full_name="Example Person",
        is_active=True,
        is_admin=is_admin,
        created_at=now,
        updated_at=now,
    )


def test_google_role_is_resolved_from_database():
    token = auth.create_session_token(
        "person@example.com",
        "user",
        subject="google:12345",
        email="person@example.com",
        name="Example Person",
        auth_provider="google",
    )
    user = asyncio.run(auth.require_auth(
        _request_with_session(token),
        _FakeDb(_managed_user(is_admin=True)),
    ))

    assert user["role"] == "admin"


def test_disabled_google_user_is_rejected():
    managed_user = _managed_user()
    managed_user.is_active = False
    token = auth.create_session_token(
        "person@example.com",
        "user",
        subject="google:12345",
        email="person@example.com",
        auth_provider="google",
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(auth.require_auth(
            _request_with_session(token),
            _FakeDb(managed_user),
        ))

    assert exc.value.status_code == 403


def test_admin_can_promote_another_user():
    managed_user = _managed_user()
    db = _FakeDb(managed_user)

    response = asyncio.run(settings_api.update_user_role(
        managed_user.id,
        settings_api.UserRoleUpdate(role="admin"),
        {
            "username": "admin@example.com",
            "email": "admin@example.com",
            "role": "admin",
        },
        db,
    ))

    assert response.role == "admin"
    assert managed_user.is_admin is True
    assert db.committed is True


def test_admin_cannot_demote_self():
    managed_user = _managed_user(is_admin=True)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(settings_api.update_user_role(
            managed_user.id,
            settings_api.UserRoleUpdate(role="user"),
            {
                "username": managed_user.email,
                "email": managed_user.email,
                "role": "admin",
            },
            _FakeDb(managed_user),
        ))

    assert exc.value.status_code == 400
