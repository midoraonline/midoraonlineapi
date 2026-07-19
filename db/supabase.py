from __future__ import annotations

import threading
from typing import Annotated

import httpx
from fastapi import Depends
from httpx import Limits
from supabase import Client, create_client
from supabase.lib.client_options import SyncClientOptions, SyncHttpxClient

from core.config import get_settings
from core.security import get_bearer_token

_admin_lock = threading.Lock()
_admin_client: Client | None = None


def _make_httpx_client() -> SyncHttpxClient:
    """Stable HTTP/1.1 client for PostgREST.

    postgrest-py defaults to http2=True. Under concurrent FastAPI traffic that
    causes frequent httpx.RemoteProtocolError / ConnectionTerminated against
    Supabase, which surfaces as 500s (and Next 502s) for engagement, views,
    chat unread, and shop metadata.
    """
    return SyncHttpxClient(
        http2=False,
        timeout=httpx.Timeout(30.0, connect=10.0),
        limits=Limits(
            max_connections=50,
            max_keepalive_connections=10,
            keepalive_expiry=30.0,
        ),
        follow_redirects=True,
    )


def _client_options() -> SyncClientOptions:
    return SyncClientOptions(httpx_client=_make_httpx_client())


def get_supabase_with_jwt(access_token: str) -> Client:
    """Return Supabase client configured with a user JWT for RLS.

    NOTE: For custom auth, configure Supabase JWT settings so that it trusts
    tokens signed with APP_JWT_SECRET and uses the `sub` claim as auth.uid().
    """
    settings = get_settings()
    client = create_client(
        settings.supabase_url,
        settings.supabase_anon_key,
        options=_client_options(),
    )
    client.auth.set_session(access_token, "")
    return client


def get_supabase_admin() -> Client:
    """Process-wide admin client (service role). Prefer reuse over create_client-per-call."""
    global _admin_client
    if _admin_client is not None:
        return _admin_client
    with _admin_lock:
        if _admin_client is None:
            settings = get_settings()
            _admin_client = create_client(
                settings.supabase_url,
                settings.supabase_service_role_key,
                options=_client_options(),
            )
        return _admin_client


def get_supabase_client(
    token: Annotated[str | None, Depends(get_bearer_token)],
) -> Client:
    """
    Dependency: returns Supabase client.

    Temporarily always uses the admin client to avoid Supabase JWT signature
    issues while custom JWT integration is being updated. This means RLS
    is evaluated with service role privileges, so do not use this in
    production until Supabase JWT is correctly configured and the
    per-user session flow (get_supabase_with_jwt) is re-enabled.
    """
    return get_supabase_admin()
