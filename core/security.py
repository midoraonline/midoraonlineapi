from typing import Annotated

import jwt
from fastapi import Cookie, Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from auth.cookies import ACCESS_COOKIE
from core.config import get_settings

security = HTTPBearer(auto_error=False)


class TokenPayload(BaseModel):
    sub: str | None = None
    role: str | None = None
    type: str | None = None


def get_bearer_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
) -> str | None:
    if credentials is None:
        return None
    if credentials.scheme != "Bearer":
        return None
    return credentials.credentials


def get_access_token(
    bearer: Annotated[str | None, Depends(get_bearer_token)],
    cookie_token: Annotated[str | None, Cookie(alias=ACCESS_COOKIE)] = None,
) -> str | None:
    """Read the access token from either the Authorization header or cookie.

    Bearer wins so we don't break SSR / mobile / server-to-server callers.
    """
    return bearer or cookie_token


def decode_auth_token(token: str) -> TokenPayload:
    """Decode and validate an app JWT. Raises 401 on any signature error."""
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.app_jwt_secret,
            algorithms=[settings.app_jwt_algorithm],
        )
        return TokenPayload(**payload)
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )


# Backwards-compat alias for existing internal callers.
_decode_auth_token = decode_auth_token


def get_current_user_id(
    token: Annotated[str | None, Depends(get_access_token)],
) -> str:
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = _decode_auth_token(token)
    if not payload.sub or payload.type != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )
    return payload.sub


def get_optional_user_id(
    token: Annotated[str | None, Depends(get_access_token)],
) -> str | None:
    if not token:
        return None
    try:
        payload = _decode_auth_token(token)
        if payload.type != "access":
            return None
        return payload.sub
    except HTTPException:
        return None


def get_current_claims(
    token: Annotated[str | None, Depends(get_access_token)],
) -> TokenPayload:
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = _decode_auth_token(token)
    if not payload.sub or payload.type != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )
    return payload


def get_optional_claims(
    token: Annotated[str | None, Depends(get_access_token)],
) -> TokenPayload | None:
    if not token:
        return None
    try:
        payload = _decode_auth_token(token)
    except HTTPException:
        return None
    if not payload.sub or payload.type != "access":
        return None
    return payload


def require_admin_role(
    token: Annotated[str | None, Depends(get_access_token)],
    x_admin_key: Annotated[str | None, Header(alias="X-Admin-Key")] = None,
) -> TokenPayload | None:
    """Admin gate for browser-facing endpoints.

    A request is allowed if EITHER
      * the authenticated user has role=admin in their JWT, OR
      * a valid X-Admin-Key header is presented (kept for ops scripts / CI).

    In production, `ADMIN_API_KEY` is required to exist at boot (see
    `core.config.get_settings`), but the browser itself should use role-based
    admin access, not the header.
    """
    settings = get_settings()

    # Script / ops fallback: X-Admin-Key without a bearer/cookie still works.
    if settings.admin_api_key and x_admin_key and x_admin_key == settings.admin_api_key:
        return None

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = _decode_auth_token(token)
    except HTTPException as exc:
        raise exc
    if payload.type != "access" or not payload.sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )
    if (payload.role or "").lower() == "admin":
        return payload
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Forbidden",
    )


# Backwards-compat alias — still accepted by existing admin routes during
# the migration. Prefer `require_admin_role` going forward.
require_admin = require_admin_role
