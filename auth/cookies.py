"""Auth cookie helpers: set / clear the access + refresh cookies consistently.

We store the JWT tokens themselves (not a session ID). The refresh cookie is
scoped to the refresh endpoints only, so regular API calls never send it.
"""
from __future__ import annotations

from fastapi import Response

from core.config import get_settings

ACCESS_COOKIE = "midora_access"
REFRESH_COOKIE = "midora_refresh"

# Path the refresh cookie is sent to. Scoped so it isn't attached to every call.
REFRESH_COOKIE_PATH = "/api/v1/auth"


def _cookie_kwargs(is_production: bool) -> dict:
    # In production we require HTTPS + SameSite=None so cross-origin XHR works
    # from the marketing domain. In dev we use Lax to keep DevTools simple.
    return {
        "httponly": True,
        "secure": is_production,
        "samesite": "none" if is_production else "lax",
    }


def set_auth_cookies(
    response: Response,
    *,
    access_token: str,
    refresh_token: str,
    access_ttl_seconds: int,
    refresh_ttl_seconds: int,
) -> None:
    settings = get_settings()
    kwargs = _cookie_kwargs(settings.is_production)
    response.set_cookie(
        ACCESS_COOKIE,
        access_token,
        max_age=access_ttl_seconds,
        path="/",
        **kwargs,
    )
    response.set_cookie(
        REFRESH_COOKIE,
        refresh_token,
        max_age=refresh_ttl_seconds,
        path=REFRESH_COOKIE_PATH,
        **kwargs,
    )


def clear_auth_cookies(response: Response) -> None:
    settings = get_settings()
    kwargs = _cookie_kwargs(settings.is_production)
    response.delete_cookie(ACCESS_COOKIE, path="/", **kwargs)
    response.delete_cookie(REFRESH_COOKIE, path=REFRESH_COOKIE_PATH, **kwargs)
