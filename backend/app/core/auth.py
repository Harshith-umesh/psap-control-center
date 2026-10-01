import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.user import User
from app.utils.logger import create_logger

logger = create_logger("Auth")

COOKIE_NAME = "session"
REQUESTER_SUBJECT_ANNOTATION = "control-center.psap.redhat.com/requester-subject"
REQUESTER_EMAIL_ANNOTATION = "control-center.psap.redhat.com/requester-email"
REQUESTER_NAME_ANNOTATION = "control-center.psap.redhat.com/requester-name"
REQUESTER_PROVIDER_ANNOTATION = "control-center.psap.redhat.com/auth-provider"


def actor_label(user: dict) -> str:
    """Human-readable, verified actor label for activity messages."""
    return user.get("email") or user.get("name") or user["username"]


def requester_annotations(user: dict) -> dict[str, str]:
    """Durable actor identity attached to resources created by the UI."""
    values = {
        REQUESTER_SUBJECT_ANNOTATION: user.get("subject", ""),
        REQUESTER_EMAIL_ANNOTATION: user.get("email", ""),
        REQUESTER_NAME_ANNOTATION: user.get("name", ""),
        REQUESTER_PROVIDER_ANNOTATION: user.get("auth_provider", "local"),
    }
    return {key: str(value) for key, value in values.items() if value}


def validate_credentials(username: str, password: str) -> Optional[dict]:
    """Check username/password against env-var accounts.
    Returns {"username": ..., "role": ...} on success, None on failure.
    """
    if secrets.compare_digest(username, settings.ADMIN_USERNAME) and \
       secrets.compare_digest(password, settings.ADMIN_PASSWORD):
        return {"username": username, "role": "admin"}

    if secrets.compare_digest(username, settings.USER_USERNAME) and \
       secrets.compare_digest(password, settings.USER_PASSWORD):
        return {"username": username, "role": "user"}

    return None


def create_session_token(
    username: str,
    role: str,
    *,
    subject: Optional[str] = None,
    email: Optional[str] = None,
    name: Optional[str] = None,
    auth_provider: str = "local",
) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload = {
        "sub": subject or f"local:{username}",
        "username": username,
        "email": email,
        "name": name or username,
        "auth_provider": auth_provider,
        "role": role,
        "exp": expire,
    }
    return jwt.encode(
        payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM
    )


def decode_session_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
        )
        # Tokens issued before Google SSO used ``sub`` as the username.
        # Keep accepting those during the rollout so existing sessions do
        # not fail abruptly when the dev deployment updates.
        subject: Optional[str] = payload.get("sub")
        username: Optional[str] = payload.get("username") or subject
        role: Optional[str] = payload.get("role")
        if subject is None or username is None or role is None:
            return None
        return {
            "subject": subject,
            "username": username,
            "email": payload.get("email"),
            "name": payload.get("name") or username,
            "auth_provider": payload.get("auth_provider") or "local",
            "role": role,
        }
    except JWTError:
        return None


def get_current_user(request: Request) -> Optional[dict]:
    """Extract user from the session cookie. Returns None if absent/invalid."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    return decode_session_token(token)


async def require_auth(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Dependency: any authenticated user (admin or user)."""
    user = get_current_user(request)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    # Google identities are persisted after their first successful sign-in.
    # Resolve their current role on every request so an administrator's role
    # change takes effect without waiting for the existing session to expire.
    if user.get("auth_provider") == "google" and user.get("email"):
        result = await db.execute(
            select(User).where(User.email == user["email"].lower())
        )
        persisted_user = result.scalar_one_or_none()
        if persisted_user is not None:
            if not persisted_user.is_active:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="User account is disabled",
                )
            user["role"] = "admin" if persisted_user.is_admin else "user"
            user["name"] = persisted_user.full_name or user["name"]
    return user


async def require_admin(user: dict = Depends(require_auth)) -> dict:
    """Dependency: admin role only."""
    if user["role"] != "admin":
        logger.warn(
            "Forbidden: user '%s' (role=%s) attempted admin action",
            user["username"], user["role"],
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user
