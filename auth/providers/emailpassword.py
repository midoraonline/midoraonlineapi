from typing import Any

from core.config import get_settings


def sign_up(
    email: str,
    password: str,
    full_name: str | None = None,
    user_role: str = "customer",
) -> dict[str, Any]:
    """Register user via Supabase Auth and create profile row. Optionally send verification email."""
    settings = get_settings()
    from supabase import create_client

    client = create_client(settings.supabase_url, settings.supabase_anon_key)
    opts: dict[str, Any] = {}
    if full_name is not None or user_role:
        opts["data"] = {"full_name": full_name or "", "user_role": user_role}
    response = client.auth.sign_up({"email": email, "password": password, "options": opts})
    user = getattr(response, "user", None)
    session = getattr(response, "session", None)
    if user:
        # Create profile row using service_role so RLS doesn't block
        admin = create_client(settings.supabase_url, settings.supabase_service_role_key)
        try:
            admin.table("profiles").insert(
                {"id": user.id, "full_name": full_name, "user_role": user_role}
            ).execute()
        except Exception:
            pass  # Profile might already exist or table missing
        return {"user": user, "session": session}
    raise ValueError(getattr(response, "message", str(response)))


def sign_in(email: str, password: str) -> dict[str, Any]:
    """Sign in via Supabase Auth. Returns session/tokens."""
    settings = get_settings()
    from supabase import create_client

    client = create_client(settings.supabase_url, settings.supabase_anon_key)
    response = client.auth.sign_in_with_password({"email": email, "password": password})
    session = getattr(response, "session", None)
    if session:
        return {
            "access_token": session.access_token,
            "refresh_token": getattr(session, "refresh_token", None) or "",
            "user": getattr(response, "user", None),
        }
    raise ValueError("Sign in failed")


def verify_email(token: str) -> dict[str, Any]:
    """Mark email as verified (custom flow or Supabase confirmation)."""
    settings = get_settings()
    from supabase import create_client

    client = create_client(settings.supabase_url, settings.supabase_anon_key)
    response = client.auth.verify_otp({"token_hash": token, "type": "email"})
    return {"verified": True, "user": getattr(response, "user", None)}


def refresh_session(refresh_token: str) -> dict[str, Any]:
    """Refresh Supabase session. Returns new tokens."""
    settings = get_settings()
    from supabase import create_client

    client = create_client(settings.supabase_url, settings.supabase_anon_key)
    response = client.auth.refresh_session(refresh_token)
    session = getattr(response, "session", None)
    if session:
        return {
            "access_token": session.access_token,
            "refresh_token": getattr(session, "refresh_token", None) or "",
        }
    raise ValueError("Refresh failed")


def get_profile(user_id: str) -> dict[str, Any] | None:
    """Fetch profile by user id (auth.users.id)."""
    settings = get_settings()
    from supabase import create_client

    client = create_client(settings.supabase_url, settings.supabase_service_role_key)
    r = client.table("profiles").select("*").eq("id", user_id).execute()
    if r.data and len(r.data) > 0:
        return r.data[0]
    return None
