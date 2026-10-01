from fastapi import APIRouter, Depends, HTTPException
from datetime import datetime
from pydantic import BaseModel
from typing import Literal, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.auth import require_admin
from app.models.user import User
from app.services.settings_service import SettingsService
from app.services.slack_notifier import send_test_message, validate_webhook_url
from app.utils.logger import create_logger

router = APIRouter()
logger = create_logger("SettingsAPI")

SLACK_WEBHOOK_KEY = "slack_webhook_url"


def _mask_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    return url[:25] + "..." + url[-6:] if len(url) > 35 else url[:10] + "..."


class SlackSettingsResponse(BaseModel):
    webhook_url_masked: Optional[str] = None
    enabled: bool = False


class SlackSettingsUpdate(BaseModel):
    webhook_url: Optional[str] = None


class UserRoleUpdate(BaseModel):
    role: Literal["admin", "user"]


class ManagedUserResponse(BaseModel):
    id: str
    username: str
    email: str
    full_name: Optional[str] = None
    role: Literal["admin", "user"]
    is_active: bool
    created_at: datetime
    updated_at: datetime


def _managed_user_response(user: User) -> ManagedUserResponse:
    return ManagedUserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        full_name=user.full_name,
        role="admin" if user.is_admin else "user",
        is_active=user.is_active,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


@router.get("/users", response_model=list[ManagedUserResponse])
async def list_users(
    _user: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).order_by(User.email.asc()))
    return [_managed_user_response(user) for user in result.scalars().all()]


@router.patch("/users/{user_id}/role", response_model=ManagedUserResponse)
async def update_user_role(
    user_id: str,
    body: UserRoleUpdate,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.id == user_id))
    managed_user = result.scalar_one_or_none()
    if managed_user is None:
        raise HTTPException(status_code=404, detail="User not found")

    if (
        body.role != "admin"
        and admin.get("email")
        and managed_user.email.lower() == admin["email"].lower()
    ):
        raise HTTPException(
            status_code=400,
            detail="You cannot remove your own administrator role",
        )

    managed_user.is_admin = body.role == "admin"
    await db.commit()
    await db.refresh(managed_user)
    logger.info(
        "User role changed for %s to %s by %s",
        managed_user.email,
        body.role,
        admin["username"],
    )
    return _managed_user_response(managed_user)


@router.get("/slack", response_model=SlackSettingsResponse)
async def get_slack_settings(
    _user: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    service = SettingsService(db)
    url = await service.get(SLACK_WEBHOOK_KEY)
    return SlackSettingsResponse(
        webhook_url_masked=_mask_url(url),
        enabled=bool(url),
    )


@router.put("/slack", response_model=SlackSettingsResponse)
async def update_slack_settings(
    body: SlackSettingsUpdate,
    _user: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    service = SettingsService(db)
    url = body.webhook_url.strip() if body.webhook_url else None
    if url:
        error = validate_webhook_url(url)
        if error:
            raise HTTPException(status_code=400, detail=error)
    await service.set(SLACK_WEBHOOK_KEY, url)
    action = "configured" if url else "cleared"
    logger.info(
        f"Slack webhook {action} by {_user['username']}"
    )
    return SlackSettingsResponse(
        webhook_url_masked=_mask_url(url),
        enabled=bool(url),
    )


@router.post("/slack/test")
async def test_slack_webhook(
    _user: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    service = SettingsService(db)
    url = await service.get(SLACK_WEBHOOK_KEY)
    if not url:
        raise HTTPException(
            status_code=400,
            detail="No Slack webhook URL configured",
        )

    success = await send_test_message(url)
    if not success:
        raise HTTPException(
            status_code=502,
            detail="Failed to send test message to Slack",
        )

    return {"status": "ok", "message": "Test message sent successfully"}
