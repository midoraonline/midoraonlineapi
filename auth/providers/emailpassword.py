from typing import Any
from uuid import uuid4

from auth import service as auth_service
from db.supabase import get_supabase_admin


def sign_up(
    email: str,
    password: str,
    full_name: str | None = None,
    user_role: str = "customer",
    *,
    user_agent: str | None = None,
    ip: str | None = None,
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
        user_agent=user_agent,
        ip=ip,
    )
    return {
        "user": user,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "verification_token": token,
    }


def sign_in(
    email: str,
    password: str,
    *,
    user_agent: str | None = None,
    ip: str | None = None,
) -> dict[str, Any]:
    """Sign in via custom users table. Returns JWT access/refresh tokens."""
    user = auth_service.authenticate_user(email, password)
    if not user:
        raise ValueError("Invalid email or password")
    access_token, refresh_token = auth_service.create_access_and_refresh_tokens(
        user_id=str(user["id"]),
        role=user.get("user_role", "customer"),
        user_agent=user_agent,
        ip=ip,
    )
    return {
        "user": user,
        "access_token": access_token,
        "refresh_token": refresh_token,
    }


def verify_email(
    token: str,
    *,
    user_agent: str | None = None,
    ip: str | None = None,
) -> dict[str, Any]:
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
        user_agent=user_agent,
        ip=ip,
    )
    return {
        "user": user,
        "access_token": access_token,
        "refresh_token": refresh_token,
    }


def refresh_session(
    refresh_token: str,
    *,
    user_agent: str | None = None,
    ip: str | None = None,
) -> dict[str, Any]:
    """Validate + rotate a refresh token. Raises on reuse / revocation."""
    access_token, new_refresh_token, claims = auth_service.rotate_refresh_token(
        refresh_token, user_agent=user_agent, ip=ip
    )
    # Sanity: ensure the user still exists. If not, revoke what we just issued.
    user_id = claims.get("sub")
    if user_id:
        client = get_supabase_admin()
        res = (
            client.table("users")
            .select("id, user_role")
            .eq("id", user_id)
            .limit(1)
            .execute()
        )
        if not res.data:
            auth_service.revoke_refresh_token(new_refresh_token)
            raise ValueError("User not found")
    return {
        "access_token": access_token,
        "refresh_token": new_refresh_token,
    }


def get_profile(user_id: str) -> dict[str, Any] | None:
    """Fetch profile: merge `users` (email, verification, auth fields) with `profiles` (avatar, phone)."""
    client = get_supabase_admin()
    user_res = (
        client.table("users")
        .select("id, email, full_name, user_role, email_verified")
        .eq("id", user_id)
        .limit(1)
        .execute()
    )
    if not user_res.data:
        return None
    user = user_res.data[0]

    prof_res = client.table("profiles").select("full_name, avatar_url, phone_number").eq("id", user_id).limit(1).execute()
    # `users.user_role` is the canonical source of truth. `profiles.user_role`
    # used to be read here too but it can drift out of sync (see promote_to_merchant),
    # so we now ignore it. promote_to_merchant keeps profiles in sync for any legacy
    # queries that still read it directly.
    if prof_res.data:
        p = prof_res.data[0]
        return {
            "id": user.get("id"),
            "email": user.get("email", ""),
            "email_verified": bool(user.get("email_verified")),
            "full_name": p.get("full_name") or user.get("full_name"),
            "avatar_url": p.get("avatar_url"),
            "phone_number": p.get("phone_number"),
            "user_role": user.get("user_role", "customer"),
        }

    return {
        "id": user.get("id"),
        "email": user.get("email", ""),
        "email_verified": bool(user.get("email_verified")),
        "full_name": user.get("full_name"),
        "avatar_url": None,
        "phone_number": None,
        "user_role": user.get("user_role", "customer"),
    }
