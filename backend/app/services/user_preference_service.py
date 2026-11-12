"""Persistence helpers for authenticated user preferences."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.user_preference import UserPreference


async def get_preference(
    session: AsyncSession,
    *,
    subject: str,
    preference_key: str,
) -> UserPreference | None:
    result = await session.execute(
        select(UserPreference).where(
            UserPreference.subject == subject,
            UserPreference.preference_key == preference_key,
        )
    )
    return result.scalar_one_or_none()


async def save_preference(
    session: AsyncSession,
    *,
    subject: str,
    preference_key: str,
    value: dict,
) -> UserPreference:
    """Create or atomically update a preference for one principal."""
    if settings.DATABASE_URL.startswith("postgresql"):
        now = datetime.now(timezone.utc)
        statement = (
            pg_insert(UserPreference)
            .values(
                subject=subject,
                preference_key=preference_key,
                value=value,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=["subject", "preference_key"],
                set_={"value": value, "updated_at": now},
            )
            .returning(UserPreference)
        )
        result = await session.execute(statement)
        return result.scalar_one()

    preference = await get_preference(
        session,
        subject=subject,
        preference_key=preference_key,
    )
    if preference is None:
        preference = UserPreference(
            subject=subject,
            preference_key=preference_key,
            value=value,
        )
        session.add(preference)
    else:
        preference.value = value
        preference.updated_at = datetime.now(timezone.utc)
    await session.flush()
    return preference
