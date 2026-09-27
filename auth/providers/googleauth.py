import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

import httpx
import jwt

from auth import service as auth_service
from core.config import get_settings
from db.supabase import get_supabase_admin

logger = logging.getLogger(__name__)

_http = httpx.Client(timeout=httpx.Timeout(8.0, connect=3.0))
_GOOGLE_ISSUERS = {"https://accounts.google.com", "accounts.google.com"}


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
        response = _http.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ValueError("Failed to exchange Google auth code") from exc

    payload = response.json()
    if "access_token" not in payload and "id_token" not in payload:
        raise ValueError("Google token response missing access_token")
    return payload


def _profile_from_id_token(id_token: str, client_id: str) -> dict[str, Any] | None:
    """Read the id_token returned by Google's token endpoint.

    The token is not taken from the browser. It arrives on the HTTPS response
    from accounts.google.com that we just called with the client secret, so
    the signature is not re-fetched from the certs endpoint.
    """
    try:
        claims = jwt.decode(id_token, options={"verify_signature": False, "verify_aud": False})
    except jwt.InvalidTokenError:
        return None
    if claims.get("iss") not in _GOOGLE_ISSUERS:
        return None
    aud = claims.get("aud")
    if aud != client_id and not (isinstance(aud, list) and client_id in aud):
        return None
    exp = claims.get("exp")
    if exp is not None and int(exp) < int(datetime.now(timezone.utc).timestamp()):
        return None
    if not claims.get("sub") or not claims.get("email"):
        return None
    return {
        "sub": claims["sub"],
        "email": claims["email"],
        "name": claims.get("name"),
        "email_verified": claims.get("email_verified"),
    }


def _fetch_google_userinfo(access_token: str) -> dict[str, Any]:
    try:
        response = _http.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
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


def _google_columns() -> str:
    return auth_service._USER_LOGIN_COLUMNS + ",google_sub"


def _get_or_create_local_user(google_profile: dict[str, Any]) -> dict[str, Any]:
    client = get_supabase_admin()
    email = str(google_profile["email"]).strip().lower()
    full_name = google_profile.get("name")
    google_sub = str(google_profile.get("sub") or "").strip() or None

    user = None
    if google_sub:
        user = auth_service.lookup_user_by("google_sub", google_sub, columns=_google_columns())
    if user is None:
        user = auth_service.lookup_user_by("email", email, columns=_google_columns())
    if user:
        updates: dict[str, Any] = {}
        if not user.get("email_verified"):
            updates["email_verified"] = True
        if not user.get("full_name") and full_name:
            updates["full_name"] = full_name
        if google_sub and not user.get("google_sub"):
            updates["google_sub"] = google_sub
        if updates:
            try:
                updated = client.table("users").update(updates).eq("id", user["id"]).execute()
                if updated.data:
                    user = updated.data[0]
            except Exception:
                updates.pop("google_sub", None)
                if updates:
                    updated = client.table("users").update(updates).eq("id", user["id"]).execute()
                    if updated.data:
                        user = updated.data[0]
        return user

    user_role = "customer"
    payload = {
        "email": email,
        "password_hash": auth_service.unusable_password_hash(),
        "full_name": full_name,
        "user_role": user_role,
        "email_verified": True,
    }
    if google_sub:
        payload["google_sub"] = google_sub
    try:
        created = client.table("users").insert(payload).execute()
    except Exception:
        payload.pop("google_sub", None)
        created = client.table("users").insert(payload).execute()
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

    started = time.perf_counter()
    token_payload = _exchange_code_for_google_tokens(code)
    exchanged = time.perf_counter()
    client_id, _, _ = _get_google_oauth_settings()
    id_token = token_payload.get("id_token")
    google_profile = _profile_from_id_token(id_token, client_id) if id_token else None
    profile_source = "id_token"
    if google_profile is None:
        if not token_payload.get("access_token"):
            raise ValueError("Google token response missing access_token")
        google_profile = _fetch_google_userinfo(token_payload["access_token"])
        profile_source = "userinfo"
    profiled = time.perf_counter()
    user = _get_or_create_local_user(google_profile)
    stored = time.perf_counter()
    access_token, refresh_token = auth_service.create_access_and_refresh_tokens(
        user_id=str(user["id"]),
        role=user.get("user_role", "customer"),
    )
    logger.info(
        "auth:google source=%s token_ms=%.1f profile_ms=%.1f db_ms=%.1f refresh_ms=%.1f total_ms=%.1f",
        profile_source,
        (exchanged - started) * 1000,
        (profiled - exchanged) * 1000,
        (stored - profiled) * 1000,
        (time.perf_counter() - stored) * 1000,
        (time.perf_counter() - started) * 1000,
    )
    return {
        "user": user,
        "access_token": access_token,
        "refresh_token": refresh_token,
    }
