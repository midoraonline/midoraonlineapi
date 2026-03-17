from typing import Annotated

from fastapi import Depends
from supabase import Client, create_client

from core.config import get_settings
from core.security import get_bearer_token


def get_supabase_with_jwt(access_token: str) -> Client:
    """Return Supabase client configured with a user JWT for RLS.

    NOTE: For custom auth, configure Supabase JWT settings so that it trusts
    tokens signed with APP_JWT_SECRET and uses the `sub` claim as auth.uid().
    """
    settings = get_settings()
    client = create_client(
        settings.supabase_url,
        settings.supabase_anon_key,
    )
    client.auth.set_session(access_token, "")
    return client


def get_supabase_admin() -> Client:
    settings = get_settings()
    return create_client(
        settings.supabase_url,
        settings.supabase_service_role_key,
    )


def get_supabase_client(
    token: Annotated[str | None, Depends(get_bearer_token)],
) -> Client:
    """Dependency: returns Supabase client with user session when Bearer token present."""
    if not token:
        return get_supabase_admin()
    return get_supabase_with_jwt(token)
