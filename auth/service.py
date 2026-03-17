from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Tuple

from jose import JWTError, jwt
from passlib.context import CryptContext

from core.config import get_settings
from db.supabase import get_supabase_admin


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _get_jwt_settings():
    settings = get_settings()
    return (
        settings.app_jwt_secret,
        settings.app_jwt_algorithm,
        settings.app_access_token_expire_minutes,
        settings.app_refresh_token_expire_days,
    )


def hash_password(password: str) -> str:
    # bcrypt only uses first 72 bytes; truncate to avoid errors on very long passwords
    return pwd_context.hash(password[:72])


def verify_password(plain_password: str, password_hash: str) -> bool:
    return pwd_context.verify(plain_password, password_hash)


def create_user(
    email: str,
    password: str,
    full_name: Optional[str] = None,
    user_role: str = "customer",
) -> dict[str, Any]:
    client = get_supabase_admin()
    normalized_email = email.strip().lower()

    # Ensure email is unique
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
    result = client.table("users").select("*").eq("email", normalized_email).limit(1).execute()
    if not result.data:
        return None
    user = result.data[0]
    if not verify_password(password, user.get("password_hash", "")):
        return None
    return user


def _create_token(
    subject: str,
    role: str,
    expires_delta: timedelta,
    token_type: str,
) -> str:
    secret, algorithm, _, _ = _get_jwt_settings()
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "role": role,
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
    }
    return jwt.encode(payload, secret, algorithm=algorithm)


def create_access_and_refresh_tokens(user_id: str, role: str) -> Tuple[str, str]:
    _, _, access_minutes, refresh_days = _get_jwt_settings()
    access_exp = timedelta(minutes=access_minutes)
    refresh_exp = timedelta(days=refresh_days)
    access = _create_token(user_id, role, access_exp, token_type="access")
    refresh = _create_token(user_id, role, refresh_exp, token_type="refresh")
    return access, refresh


def decode_token(token: str) -> dict[str, Any]:
    secret, algorithm, _, _ = _get_jwt_settings()
    try:
        payload = jwt.decode(token, secret, algorithms=[algorithm])
        return payload
    except JWTError as exc:
        raise ValueError("Invalid token") from exc

