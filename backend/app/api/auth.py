import secrets
import base64
import hashlib
import asyncio
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from google.auth import exceptions as google_auth_exceptions
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from jose import JWTError, jwt
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import (
    validate_credentials,
    create_session_token,
    require_auth,
    COOKIE_NAME,
)
from app.core.config import settings
from app.core.database import get_db
from app.models.user import User
from app.utils.logger import create_logger

router = APIRouter()
logger = create_logger("AuthAPI")

OAUTH_STATE_COOKIE = "google_oauth_state"
GOOGLE_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}


class LoginRequest(BaseModel):
    username: str
    password: str


class GoogleCallbackRequest(BaseModel):
    code: str = Field(min_length=1)
    state: str = Field(min_length=1)


@router.post("/login")
async def login(body: LoginRequest, request: Request):
    if not settings.LOCAL_LOGIN_ENABLED:
        raise HTTPException(status_code=404, detail="Local login is disabled")
    user = validate_credentials(body.username, body.password)
    if user is None:
        logger.warn(f"Failed login attempt for user: {body.username}")
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid credentials"},
        )

    token = create_session_token(
        user["username"],
        user["role"],
        subject=f"local:{user['username']}",
        name=user["username"],
        auth_provider="local",
    )

    is_secure = request.url.scheme == "https" or \
        request.headers.get("x-forwarded-proto") == "https"

    response = JSONResponse(content={
        "username": user["username"],
        "email": None,
        "name": user["username"],
        "auth_provider": "local",
        "role": user["role"],
    })
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=is_secure,
        samesite="lax",
        path="/api",
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )

    logger.info(f"User '{user['username']}' logged in (role={user['role']})")
    return response


def _secure_request(request: Request) -> bool:
    return request.url.scheme == "https" or \
        request.headers.get("x-forwarded-proto") == "https"


def _session_response(user: dict, request: Request) -> JSONResponse:
    token = create_session_token(
        user["username"],
        user["role"],
        subject=user["subject"],
        email=user.get("email"),
        name=user.get("name"),
        auth_provider=user.get("auth_provider", "google"),
    )
    response = JSONResponse(content={
        key: user.get(key)
        for key in ("username", "email", "name", "auth_provider", "role")
    })
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=_secure_request(request),
        samesite="lax",
        path="/api",
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )
    return response


@router.get("/config")
async def auth_config():
    """Public feature flags needed to render the sign-in choices."""
    return {
        "google_enabled": settings.GOOGLE_OAUTH_ENABLED,
        "local_login_enabled": settings.LOCAL_LOGIN_ENABLED,
    }


@router.get("/google/login")
async def google_login(request: Request):
    if not settings.GOOGLE_OAUTH_ENABLED:
        raise HTTPException(status_code=404, detail="Google login is disabled")

    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).rstrip(b"=").decode()
    expires = datetime.now(timezone.utc) + timedelta(minutes=10)
    state_token = jwt.encode(
        {
            "state": state,
            "nonce": nonce,
            "code_verifier": code_verifier,
            "type": "google_oauth",
            "exp": expires,
        },
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": settings.GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "prompt": "select_account",
        "hd": settings.GOOGLE_ALLOWED_DOMAIN,
    }
    response = RedirectResponse(
        "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)
    )
    response.set_cookie(
        key=OAUTH_STATE_COOKIE,
        value=state_token,
        httponly=True,
        secure=_secure_request(request),
        samesite="lax",
        path="/",
        max_age=600,
    )
    # Remove the narrower cookie used by the first development implementation.
    response.delete_cookie(key=OAUTH_STATE_COOKIE, path="/api")
    return response


async def _verify_google_id_token(id_token: str, expected_nonce: str) -> dict:
    try:
        claims = await asyncio.to_thread(
            google_id_token.verify_oauth2_token,
            id_token,
            google_requests.Request(),
            settings.GOOGLE_CLIENT_ID,
        )
    except google_auth_exceptions.TransportError as exc:
        logger.warn("Google callback failed: signing keys unavailable")
        raise HTTPException(502, "Unable to validate Google identity") from exc
    except (ValueError, google_auth_exceptions.GoogleAuthError) as exc:
        logger.warn("Google callback rejected: ID-token verification failed")
        raise HTTPException(401, "Google identity verification failed") from exc

    if claims.get("iss") not in GOOGLE_ISSUERS:
        logger.warn("Google callback rejected: invalid issuer")
        raise HTTPException(401, "Google identity issuer is invalid")
    if not secrets.compare_digest(str(claims.get("nonce", "")), expected_nonce):
        logger.warn("Google callback rejected: nonce mismatch")
        raise HTTPException(401, "Google login nonce is invalid")
    if claims.get("email_verified") is not True:
        logger.warn("Google callback rejected: email is not verified")
        raise HTTPException(403, "A verified organization email is required")
    if claims.get("hd", "").lower() != settings.GOOGLE_ALLOWED_DOMAIN.lower():
        logger.warn("Google callback rejected: Workspace domain mismatch")
        raise HTTPException(403, "This Google Workspace organization is not allowed")
    return claims


@router.post("/google/callback")
async def google_callback(
    request: Request,
    body: GoogleCallbackRequest,
    db: AsyncSession = Depends(get_db),
):
    if not settings.GOOGLE_OAUTH_ENABLED:
        raise HTTPException(status_code=404, detail="Google login is disabled")

    state_token = request.cookies.get(OAUTH_STATE_COOKIE)
    if not state_token:
        logger.warn("Google callback rejected: missing state cookie")
        raise HTTPException(401, "Google login session expired; please try again")
    try:
        state_claims = jwt.decode(
            state_token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
        )
    except JWTError as exc:
        logger.warn("Google callback rejected: invalid state cookie")
        raise HTTPException(401, "Google login session is invalid") from exc
    if state_claims.get("type") != "google_oauth" or not secrets.compare_digest(
        str(state_claims.get("state", "")), body.state
    ):
        logger.warn("Google callback rejected: state mismatch")
        raise HTTPException(401, "Google login state is invalid")

    async with httpx.AsyncClient(timeout=10.0) as client:
        token_response = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": body.code,
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "redirect_uri": settings.GOOGLE_REDIRECT_URI,
                "grant_type": "authorization_code",
                "code_verifier": state_claims["code_verifier"],
            },
        )
    if token_response.status_code != 200:
        error_code = token_response.json().get("error", "unknown_error")
        logger.warn(f"Google authorization-code exchange failed: {error_code}")
        raise HTTPException(401, "Google login could not be completed")
    id_token = token_response.json().get("id_token")
    if not id_token:
        logger.warn("Google callback rejected: ID token missing")
        raise HTTPException(401, "Google did not return an identity token")

    claims = await _verify_google_id_token(id_token, state_claims["nonce"])
    email = claims["email"].lower()
    admin_emails = {
        item.strip().lower()
        for item in settings.GOOGLE_ADMIN_EMAILS.split(",")
        if item.strip()
    }
    result = await db.execute(select(User).where(User.email == email))
    persisted_user = result.scalar_one_or_none()
    if persisted_user is None:
        persisted_user = User(
            username=email,
            email=email,
            full_name=claims.get("name") or email,
            is_active=True,
            is_admin=email in admin_emails,
        )
        db.add(persisted_user)
    else:
        # The database role is authoritative after the first sign-in. This
        # lets admins manage access without editing deployment configuration.
        persisted_user.username = email
        persisted_user.full_name = claims.get("name") or persisted_user.full_name
    await db.commit()

    if not persisted_user.is_active:
        logger.warn("Google callback rejected: user account is disabled")
        raise HTTPException(403, "User account is disabled")

    user = {
        "subject": f"google:{claims['sub']}",
        "username": email,
        "email": email,
        "name": claims.get("name") or email,
        "auth_provider": "google",
        "role": "admin" if persisted_user.is_admin else "user",
    }
    response = _session_response(user, request)
    response.delete_cookie(key=OAUTH_STATE_COOKIE, path="/")
    response.delete_cookie(key=OAUTH_STATE_COOKIE, path="/api")
    logger.info(f"Google user logged in (role={user['role']})")
    return response


@router.post("/logout")
async def logout():
    response = JSONResponse(content={"detail": "Logged out"})
    response.delete_cookie(
        key=COOKIE_NAME,
        path="/api",
    )
    return response


@router.get("/me")
async def me(user: dict = Depends(require_auth)):
    return {
        key: user.get(key)
        for key in ("username", "email", "name", "auth_provider", "role")
    }
