"""Durable UI preferences keyed by authenticated principal and preference name."""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, JSON, String

from app.core.database import Base


class UserPreference(Base):
    __tablename__ = "user_preferences"

    subject = Column(String(255), primary_key=True)
    preference_key = Column(String(100), primary_key=True)
    value = Column(JSON, nullable=False, default=dict)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
