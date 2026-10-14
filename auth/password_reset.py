"""Forgot-password flow for custom email/password accounts.

The email carries a random token; only its SHA-256 hash is stored. Tokens
last 60 minutes and work once. A reset signs out every other session.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from db.supabase import get_supabase_admin

TOKEN_TTL_MINUTES = 60


class ResetError(ValueError):
    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_reset_token(email: str) -> tuple[str, dict[str, Any]] | None:
    """Return (token, user) or None when no password account matches.

    Callers must answer the same way either way, so emails can't be probed.
    """
    client = get_supabase_admin()
    r = (
        client.table("users")
        .select("id,email,full_name")
        .ilike("email", email.strip())
        .limit(1)
        .execute()
    )
    if not r.data:
        return None
    user = r.data[0]
    token = secrets.token_urlsafe(32)
    client.table("password_reset_tokens").insert(
        {
            "user_id": user["id"],
            "token_hash": hash_token(token),
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=TOKEN_TTL_MINUTES)).isoformat(),
        }
    ).execute()
    return token, user


def reset_password(token: str, new_password: str) -> str:
    """Set the new password. Returns the user id."""
    from auth.service import hash_password

    client = get_supabase_admin()
    r = (
        client.table("password_reset_tokens")
        .select("id,user_id,expires_at,used_at")
        .eq("token_hash", hash_token(token.strip()))
        .limit(1)
        .execute()
    )
    if not r.data:
        raise ResetError("This reset link is not valid. Request a new one.", "reset_invalid")
    row = r.data[0]
    if row.get("used_at"):
        raise ResetError("This reset link was already used. Request a new one.", "reset_used")
    expires = datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00"))
    if expires < datetime.now(timezone.utc):
        raise ResetError("This reset link has expired. Request a new one.", "reset_expired")

    now = datetime.now(timezone.utc).isoformat()
    user_id = str(row["user_id"])
    client.table("users").update({"password_hash": hash_password(new_password)}).eq("id", user_id).execute()
    client.table("password_reset_tokens").update({"used_at": now}).eq("user_id", user_id).is_("used_at", None).execute()
    try:
        client.table("refresh_tokens").update({"revoked_at": now}).eq("user_id", user_id).is_("revoked_at", None).execute()
    except Exception:
        pass
    return user_id
