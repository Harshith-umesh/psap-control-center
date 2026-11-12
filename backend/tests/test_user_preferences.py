import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from app.api import clusters as clusters_api
from app.schemas.cluster import RefreshDisplayPreferenceUpdate
from app.services import user_preference_service


def test_refresh_display_mode_is_allowlisted():
    assert RefreshDisplayPreferenceUpdate(mode="countdown").mode == "countdown"
    assert RefreshDisplayPreferenceUpdate(mode="last_update").mode == "last_update"

    with pytest.raises(ValidationError):
        RefreshDisplayPreferenceUpdate(mode="arbitrary")


def test_new_preference_is_keyed_by_subject_and_name(monkeypatch):
    monkeypatch.setattr(
        user_preference_service.settings,
        "DATABASE_URL",
        "sqlite+aiosqlite:///test.db",
    )
    lookup = MagicMock()
    lookup.scalar_one_or_none.return_value = None
    session = MagicMock()
    session.execute = AsyncMock(return_value=lookup)
    session.flush = AsyncMock()

    preference = asyncio.run(user_preference_service.save_preference(
        session,
        subject="google:immutable-subject",
        preference_key="clusters.refresh_display_mode",
        value={"mode": "last_update"},
    ))

    assert preference.subject == "google:immutable-subject"
    assert preference.preference_key == "clusters.refresh_display_mode"
    assert preference.value == {"mode": "last_update"}
    session.add.assert_called_once_with(preference)
    session.flush.assert_awaited_once()


def test_postgres_preference_save_is_atomic_upsert(monkeypatch):
    monkeypatch.setattr(
        user_preference_service.settings,
        "DATABASE_URL",
        "postgresql+asyncpg://test/test",
    )
    expected = object()
    result = MagicMock()
    result.scalar_one.return_value = expected
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)

    saved = asyncio.run(user_preference_service.save_preference(
        session,
        subject="google:immutable-subject",
        preference_key="clusters.refresh_display_mode",
        value={"mode": "countdown"},
    ))

    statement = session.execute.await_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT (subject, preference_key) DO UPDATE" in sql
    assert saved is expected


def test_missing_refresh_preference_defaults_to_countdown(monkeypatch):
    monkeypatch.setattr(
        clusters_api.user_preference_service,
        "get_preference",
        AsyncMock(return_value=None),
    )

    db = object()
    response = asyncio.run(clusters_api.get_refresh_display_preference(
        user={"subject": "google:user-one"},
        db=db,
    ))

    assert response.mode == "countdown"
    clusters_api.user_preference_service.get_preference.assert_awaited_once_with(
        db,
        subject="google:user-one",
        preference_key=clusters_api.REFRESH_DISPLAY_PREFERENCE_KEY,
    )


def test_refresh_preference_update_uses_authenticated_subject(monkeypatch):
    timestamp = datetime.now(timezone.utc)
    saved = SimpleNamespace(updated_at=timestamp)
    save = AsyncMock(return_value=saved)
    monkeypatch.setattr(
        clusters_api.user_preference_service,
        "save_preference",
        save,
    )
    db = MagicMock()
    db.commit = AsyncMock()

    response = asyncio.run(clusters_api.update_refresh_display_preference(
        RefreshDisplayPreferenceUpdate(mode="last_update"),
        user={"subject": "google:user-one"},
        db=db,
    ))

    assert response.mode == "last_update"
    assert response.updated_at == timestamp
    save.assert_awaited_once_with(
        db,
        subject="google:user-one",
        preference_key=clusters_api.REFRESH_DISPLAY_PREFERENCE_KEY,
        value={"mode": "last_update"},
    )
    db.commit.assert_awaited_once()
