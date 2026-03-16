from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel

from core.config import get_settings

security = HTTPBearer(auto_error=False)


class TokenPayload(BaseModel):
    sub: str | None = None
    email: str | None = None
    role: str | None = None


def get_bearer_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
) -> str | None:
    if credentials is None:
        return None
    if credentials.scheme != "Bearer":
        return None
    return credentials.credentials


def get_current_user_id(token: Annotated[str | None, Depends(get_bearer_token)]) -> str:
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    settings = get_settings()
    # Supabase JWT: no shared secret; we optionally decode for local claims only.
    # For RLS, the token is passed to Supabase client via set_session.
    try:
        payload = jwt.decode(
            token,
            options={"verify_signature": False},
        )
        sub = payload.get("sub")
        if not sub:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
            )
        return sub
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )


def get_optional_user_id(
    token: Annotated[str | None, Depends(get_bearer_token)],
) -> str | None:
    if not token:
        return None
    try:
        payload = jwt.decode(
            token,
            options={"verify_signature": False},
        )
        return payload.get("sub")
    except JWTError:
        return None


def require_admin(
    x_admin_key: Annotated[str | None, Header(alias="X-Admin-Key")] = None,
) -> None:
    """Dependency: allow access only if X-Admin-Key matches ADMIN_API_KEY. If ADMIN_API_KEY is unset, allow (dev)."""
    settings = get_settings()
    if not settings.admin_api_key:
        return
    if not x_admin_key or x_admin_key != settings.admin_api_key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden",
        )
