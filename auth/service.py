from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Tuple
from uuid import uuid4
import logging

import bcrypt
import jwt

from core.config import get_settings
from db.supabase import get_supabase_admin


logger = logging.getLogger(__name__)


def _get_jwt_settings() -> tuple[str, str, int, int]:
    settings = get_settings()
    return (
        settings.app_jwt_secret,
        settings.app_jwt_algorithm,
        settings.app_access_token_expire_minutes,
        settings.app_refresh_token_expire_days,
    )


_BCRYPT_MAX_BYTES = 72


def _normalise_password_bytes(password: str) -> bytes:
    return password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(password: str) -> str:
    hashed = bcrypt.hashpw(_normalise_password_bytes(password), bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(
            _normalise_password_bytes(plain_password),
            password_hash.encode("utf-8"),
        )
    except ValueError:
        return False


def create_user(
    email: str,
    password: str,
    full_name: Optional[str] = None,
    user_role: str = "customer",
) -> dict[str, Any]:
    client = get_supabase_admin()
    normalized_email = email.strip().lower()

    existing = client.table("users").select("id").eq("email", normalized_email).execute()
    if existing.data:
        raise ValueError("Email already registered")

    password_hash = hash_password(password)
    payload = {
        "email": normalized_email,
        "password_hash": password_hash,
        "full_name": full_name,
        "user_role": user_role or "customer",
    }
    result = client.table("users").insert(payload).execute()
    if not result.data:
        raise ValueError("Failed to create user")
    return result.data[0]


def authenticate_user(email: str, password: str) -> Optional[dict[str, Any]]:
    client = get_supabase_admin()
    normalized_email = email.strip().lower()
    result = (
        client.table("users")
        .select("*")
        .eq("email", normalized_email)
        .limit(1)
        .execute()
    )
    if not result.data:
        return None
    user = result.data[0]
    if not verify_password(password, user.get("password_hash", "")):
        return None
    return user


# ---------------------------------------------------------------------------
# Token TTL helpers
# ---------------------------------------------------------------------------


def access_ttl_seconds() -> int:
    _, _, minutes, _ = _get_jwt_settings()
    return int(minutes) * 60


def refresh_ttl_seconds() -> int:
    _, _, _, days = _get_jwt_settings()
    return int(days) * 24 * 60 * 60


# ---------------------------------------------------------------------------
# JWT creation / decoding
# ---------------------------------------------------------------------------


def _encode_jwt(payload: dict[str, Any]) -> str:
    secret, algorithm, _, _ = _get_jwt_settings()
    return jwt.encode(payload, secret, algorithm=algorithm)


def decode_token(token: str) -> dict[str, Any]:
    secret, algorithm, _, _ = _get_jwt_settings()
    try:
        return jwt.decode(token, secret, algorithms=[algorithm])
    except jwt.InvalidTokenError as exc:
        raise ValueError("Invalid token") from exc


def _build_access_claims(user_id: str, role: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    return {
        "sub": user_id,
        "role": role,
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=access_ttl_seconds())).timestamp()),
    }


# ---------------------------------------------------------------------------
# Supabase Realtime JWT
# ---------------------------------------------------------------------------
#
# Supabase Realtime authorises subscribers via a standard Supabase JWT.
# The project's `SUPABASE_JWT_SECRET` (Supabase Studio → Project Settings →
# API keys) must be set to `APP_JWT_SECRET` so Supabase trusts tokens signed
# by this service. The `role: "authenticated"` claim tells Supabase to run
# RLS as the `authenticated` role with `auth.uid()` = `sub`.
#
# We issue this token separately (never for auth flows) so the FastAPI
# session role (customer/merchant/admin) is preserved on the app JWT.
# Callers should refresh it on the same cadence as the app access token
# (default: every 60 minutes).

SUPABASE_REALTIME_TTL_SECONDS = 60 * 60


def create_supabase_realtime_jwt(user_id: str) -> str:
    """Mint a Supabase-compatible JWT for use with `supabase.realtime.setAuth()`."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "role": "authenticated",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=SUPABASE_REALTIME_TTL_SECONDS)).timestamp()),
    }
    return _encode_jwt(payload)


def _build_refresh_claims(user_id: str, role: str, jti: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    return {
        "sub": user_id,
        "role": role,
        "type": "refresh",
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=refresh_ttl_seconds())).timestamp()),
    }


# ---------------------------------------------------------------------------
# Refresh token registry (persisted)
# ---------------------------------------------------------------------------


def _insert_refresh_record(
    user_id: str,
    jti: str,
    expires_at: datetime,
    *,
    user_agent: str | None = None,
    ip: str | None = None,
    replaced_by: str | None = None,
) -> None:
    client = get_supabase_admin()
    try:
        client.table("refresh_tokens").insert(
            {
                "jti": jti,
                "user_id": user_id,
                "expires_at": expires_at.isoformat(),
                "user_agent": user_agent,
                "ip": ip,
                "replaced_by": replaced_by,
            }
        ).execute()
    except Exception as exc:  # noqa: BLE001 — table may not exist yet
        logger.warning(
            "Could not record refresh token jti=%s (table missing?): %s", jti, exc
        )


def _fetch_refresh_record(jti: str) -> dict[str, Any] | None:
    client = get_supabase_admin()
    try:
        r = client.table("refresh_tokens").select("*").eq("jti", jti).limit(1).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("refresh_tokens table unavailable (stateless mode): %s", exc)
        return None
    return r.data[0] if r.data else None


def _revoke_refresh_record(jti: str, replaced_by: str | None = None) -> None:
    client = get_supabase_admin()
    try:
        client.table("refresh_tokens").update(
            {
                "revoked_at": datetime.now(timezone.utc).isoformat(),
                "replaced_by": replaced_by,
            }
        ).eq("jti", jti).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not revoke refresh jti=%s: %s", jti, exc)


def _revoke_all_user_refresh_records(user_id: str) -> None:
    """Revoke every active refresh token for a user (e.g. on reuse detection)."""
    client = get_supabase_admin()
    try:
        client.table("refresh_tokens").update(
            {"revoked_at": datetime.now(timezone.utc).isoformat()}
        ).eq("user_id", user_id).is_("revoked_at", None).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not revoke all refresh tokens for %s: %s", user_id, exc)


# ---------------------------------------------------------------------------
# Public token API
# ---------------------------------------------------------------------------


def create_access_and_refresh_tokens(
    user_id: str,
    role: str,
    *,
    user_agent: str | None = None,
    ip: str | None = None,
) -> Tuple[str, str]:
    access = _encode_jwt(_build_access_claims(user_id, role))
    jti = uuid4().hex
    refresh = _encode_jwt(_build_refresh_claims(user_id, role, jti))
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=refresh_ttl_seconds())
    _insert_refresh_record(user_id, jti, expires_at, user_agent=user_agent, ip=ip)
    return access, refresh


def rotate_refresh_token(
    refresh_token: str,
    *,
    user_agent: str | None = None,
    ip: str | None = None,
) -> Tuple[str, str, dict[str, Any]]:
    """Validate + rotate a refresh token. Returns (access, refresh, decoded_claims).

    Raises `ValueError` on any failure. Implements reuse-detection: if a
    previously-rotated (revoked) token is presented, every refresh token for
    that user is revoked.
    """
    try:
        claims = decode_token(refresh_token)
    except ValueError as exc:
        raise ValueError("Invalid refresh token") from exc

    if claims.get("type") != "refresh":
        raise ValueError("Invalid refresh token")
    user_id = str(claims.get("sub") or "")
    jti = str(claims.get("jti") or "")
    if not user_id:
        raise ValueError("Invalid refresh token")

    # If the table is present, enforce single-use.
    record = _fetch_refresh_record(jti) if jti else None
    if record is not None:
        if record.get("revoked_at"):
            # Reuse of a revoked token — revoke the whole family.
            logger.warning("Refresh token reuse detected for user %s", user_id)
            _revoke_all_user_refresh_records(user_id)
            raise ValueError("Refresh token reuse detected")

    # Re-read the live role from the DB so promotions (customer → merchant,
    # manual admin changes, etc.) take effect on the next refresh without
    # forcing the user to log out.
    role = get_user_role(user_id) or str(claims.get("role") or "customer")

    # Mint new pair
    access = _encode_jwt(_build_access_claims(user_id, role))
    new_jti = uuid4().hex
    new_refresh = _encode_jwt(_build_refresh_claims(user_id, role, new_jti))
    new_expires = datetime.now(timezone.utc) + timedelta(seconds=refresh_ttl_seconds())
    _insert_refresh_record(user_id, new_jti, new_expires, user_agent=user_agent, ip=ip, replaced_by=None)

    if jti:
        _revoke_refresh_record(jti, replaced_by=new_jti)

    return access, new_refresh, claims


def revoke_refresh_token(refresh_token: str) -> None:
    try:
        claims = decode_token(refresh_token)
    except ValueError:
        return
    jti = str(claims.get("jti") or "")
    if jti:
        _revoke_refresh_record(jti)


# ---------------------------------------------------------------------------
# Role helpers
# ---------------------------------------------------------------------------


def get_user_role(user_id: str) -> str:
    """Read the canonical user_role from the users table. Defaults to customer."""
    client = get_supabase_admin()
    try:
        res = (
            client.table("users")
            .select("user_role")
            .eq("id", user_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read role for %s: %s", user_id, exc)
        return "customer"
    if not res.data:
        return "customer"
    return str(res.data[0].get("user_role") or "customer").lower()


def promote_to_merchant(user_id: str) -> tuple[str, bool]:
    """Upgrade a user to the `merchant` role when they take a merchant action
    (e.g. opening a shop). Admins are never downgraded; users who are already
    merchants or admins are left alone.

    Returns `(resulting_role, changed)` so callers can decide whether to
    re-issue auth cookies.
    """
    current = get_user_role(user_id)
    if current in ("merchant", "admin"):
        return current, False

    client = get_supabase_admin()
    try:
        res = (
            client.table("users")
            .update({"user_role": "merchant"})
            .eq("id", user_id)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not promote user %s to merchant: %s", user_id, exc)
        return current, False

    if not res.data:
        return current, False

    # Best-effort mirror into profiles.user_role so any legacy reader stays
    # consistent. We don't fail the promotion if this mirror update errors.
    try:
        client.table("profiles").update({"user_role": "merchant"}).eq("id", user_id).execute()
    except Exception as exc:  # noqa: BLE001
        logger.debug("profiles.user_role mirror update failed for %s: %s", user_id, exc)

    return "merchant", True
