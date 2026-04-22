from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

import httpx
import jwt

from auth import service as auth_service
from core.config import get_settings
from db.supabase import get_supabase_admin


GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
GOOGLE_SCOPES = "openid email profile"


def generate_state_token() -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "type": "google_oauth_state",
        "nonce": uuid4().hex,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=10)).timestamp()),
    }
    return jwt.encode(payload, settings.app_jwt_secret, algorithm=settings.app_jwt_algorithm)


def _validate_state_token(state: str) -> None:
    settings = get_settings()
    try:
        payload = jwt.decode(
            state,
            settings.app_jwt_secret,
            algorithms=[settings.app_jwt_algorithm],
        )
    except jwt.InvalidTokenError as exc:
        raise ValueError("Invalid Google OAuth state") from exc
    if payload.get("type") != "google_oauth_state":
        raise ValueError("Invalid Google OAuth state")


def _get_google_oauth_settings() -> tuple[str, str, str]:
    settings = get_settings()
    client_id = settings.google_oauth_client_id.strip()
    client_secret = settings.google_oauth_client_secret.strip()
    redirect_uri = settings.google_oauth_redirect_uri.strip()
    if not client_id or not client_secret or not redirect_uri:
        raise ValueError(
            "Google OAuth is not configured. Set GOOGLE_OAUTH_CLIENT_ID, "
            "GOOGLE_OAUTH_CLIENT_SECRET and GOOGLE_OAUTH_REDIRECT_URI."
        )
    return client_id, client_secret, redirect_uri


def get_redirect_url(state: str | None = None) -> str:
    client_id, _, redirect_uri = _get_google_oauth_settings()
    safe_state = state or generate_state_token()
    params: dict[str, str] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": GOOGLE_SCOPES,
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
    }
    params["state"] = safe_state
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def _exchange_code_for_google_tokens(code: str) -> dict[str, Any]:
    client_id, client_secret, redirect_uri = _get_google_oauth_settings()
    try:
        response = httpx.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=20.0,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ValueError("Failed to exchange Google auth code") from exc

    payload = response.json()
    if "access_token" not in payload:
        raise ValueError("Google token response missing access_token")
    return payload


def _fetch_google_userinfo(access_token: str) -> dict[str, Any]:
    try:
        response = httpx.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=20.0,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ValueError("Failed to fetch Google user profile") from exc

    profile = response.json()
    if not profile.get("sub"):
        raise ValueError("Google profile missing sub identifier")
    if not profile.get("email"):
        raise ValueError("Google profile missing email")
    return profile


def _get_or_create_local_user(google_profile: dict[str, Any]) -> dict[str, Any]:
    client = get_supabase_admin()
    email = str(google_profile["email"]).strip().lower()
    full_name = google_profile.get("name")

    existing = client.table("users").select("*").eq("email", email).limit(1).execute()
    if existing.data:
        user = existing.data[0]
        updates: dict[str, Any] = {}
        if not user.get("email_verified"):
            updates["email_verified"] = True
        if not user.get("full_name") and full_name:
            updates["full_name"] = full_name
        if updates:
            user = client.table("users").update(updates).eq("id", user["id"]).execute().data[0]
        return user

    user_role = "customer"
    # users.password_hash is required by current schema; generate an unusable random hash.
    random_password_hash = auth_service.hash_password(uuid4().hex)
    created = (
        client.table("users")
        .insert(
            {
                "email": email,
                "password_hash": random_password_hash,
                "full_name": full_name,
                "user_role": user_role,
                "email_verified": True,
            }
        )
        .execute()
    )
    if not created.data:
        raise ValueError("Failed to create local user for Google sign-in")
    user = created.data[0]

    try:
        client.table("profiles").insert(
            {
                "id": user["id"],
                "full_name": user.get("full_name"),
                "user_role": user.get("user_role", user_role),
            }
        ).execute()
    except Exception:
        pass
    return user


def handle_callback(code: str, state: str | None = None) -> dict[str, Any]:
    if not code:
        raise ValueError("Missing Google authorization code")
    if state:
        _validate_state_token(state)

    token_payload = _exchange_code_for_google_tokens(code)
    google_profile = _fetch_google_userinfo(token_payload["access_token"])
    user = _get_or_create_local_user(google_profile)
    access_token, refresh_token = auth_service.create_access_and_refresh_tokens(
        user_id=str(user["id"]),
        role=user.get("user_role", "customer"),
    )
    return {
        "user": user,
        "access_token": access_token,
        "refresh_token": refresh_token,
    }
