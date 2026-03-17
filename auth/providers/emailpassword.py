from typing import Any
from uuid import uuid4

from auth import service as auth_service
from db.supabase import get_supabase_admin


def sign_up(
    email: str,
    password: str,
    full_name: str | None = None,
    user_role: str = "customer",
) -> dict[str, Any]:
    """Register user via custom auth and create profile row if needed.

    Also creates an email verification token record for custom verification flow.
    """
    user = auth_service.create_user(
        email=email,
        password=password,
        full_name=full_name,
        user_role=user_role,
    )

    client = get_supabase_admin()
    # Optionally mirror into profiles table for existing domain logic
    try:
        client.table("profiles").insert(
            {
                "id": user["id"],
                "full_name": user.get("full_name"),
                "user_role": user.get("user_role", "customer"),
            }
        ).execute()
    except Exception:
        # Profile might already exist or table missing; do not fail registration
        pass

    # Create verification token row
    token = uuid4().hex
    client.table("email_verification_tokens").insert(
        {
            "user_id": user["id"],
            "token": token,
        }
    ).execute()

    access_token, refresh_token = auth_service.create_access_and_refresh_tokens(
        user_id=str(user["id"]),
        role=user.get("user_role", "customer"),
    )
    return {
        "user": user,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "verification_token": token,
    }


def sign_in(email: str, password: str) -> dict[str, Any]:
    """Sign in via custom users table. Returns JWT access/refresh tokens."""
    user = auth_service.authenticate_user(email, password)
    if not user:
        raise ValueError("Invalid email or password")
    access_token, refresh_token = auth_service.create_access_and_refresh_tokens(
        user_id=str(user["id"]),
        role=user.get("user_role", "customer"),
    )
    return {
        "user": user,
        "access_token": access_token,
        "refresh_token": refresh_token,
    }


def verify_email(token: str) -> dict[str, Any]:
    """Mark email as verified using custom token table and return user + fresh tokens."""
    client = get_supabase_admin()
    r = (
        client.table("email_verification_tokens")
        .select("user_id, used_at")
        .eq("token", token)
        .limit(1)
        .execute()
    )
    if not r.data:
        raise ValueError("Invalid or expired verification token")
    row = r.data[0]
    if row.get("used_at") is not None:
        raise ValueError("Verification token has already been used")

    user_id = row["user_id"]
    # Mark user as verified
    client.table("users").update({"email_verified": True}).eq("id", user_id).execute()
    # Mark token as used
    client.table("email_verification_tokens").update({"used_at": "now()"}).eq("token", token).execute()

    # Load user details and issue fresh tokens
    user_res = client.table("users").select("id,email,full_name,user_role,email_verified").eq("id", user_id).limit(1).execute()
    if not user_res.data:
        raise ValueError("User not found for verification token")
    user = user_res.data[0]
    access_token, refresh_token = auth_service.create_access_and_refresh_tokens(
        user_id=str(user["id"]),
        role=user.get("user_role", "customer"),
    )
    return {
        "user": user,
        "access_token": access_token,
        "refresh_token": refresh_token,
    }


def refresh_session(refresh_token: str) -> dict[str, Any]:
    """Validate refresh token and return new access/refresh pair."""
    payload = auth_service.decode_token(refresh_token)
    if payload.get("type") != "refresh":
        raise ValueError("Invalid refresh token")
    user_id = payload.get("sub")
    role = payload.get("role", "customer")
    if not user_id:
        raise ValueError("Invalid refresh token payload")
    # Optionally ensure user still exists
    client = get_supabase_admin()
    res = client.table("users").select("id, user_role").eq("id", user_id).limit(1).execute()
    if not res.data:
        raise ValueError("User not found")
    db_user = res.data[0]
    new_role = db_user.get("user_role", role)
    access_token, new_refresh_token = auth_service.create_access_and_refresh_tokens(
        user_id=str(db_user["id"]),
        role=new_role,
    )
    return {
        "access_token": access_token,
        "refresh_token": new_refresh_token,
    }


def get_profile(user_id: str) -> dict[str, Any] | None:
    """Fetch profile by user id (custom users.id), joined with profiles if present."""
    client = get_supabase_admin()
    # Try to pull profile; fall back to users row only
    profile = (
        client.table("profiles").select("*").eq("id", user_id).limit(1).execute()
    )
    if profile.data:
        return profile.data[0]

    user_res = client.table("users").select("id, email, full_name, user_role").eq("id", user_id).limit(1).execute()
    if user_res.data:
        row = user_res.data[0]
        return {
            "id": row.get("id"),
            "full_name": row.get("full_name"),
            "avatar_url": None,
            "phone_number": None,
            "user_role": row.get("user_role", "customer"),
        }
    return None
