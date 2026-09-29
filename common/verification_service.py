"""OTP codes for phone (SMS), shop WhatsApp, and account email.

`verification_codes.phone_number` stores the contact being proved (E.164
phone or email). Callers update users.phone_verified / email_verified /
shops.whatsapp_verified after confirm.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from db.supabase import get_supabase_admin

logger = logging.getLogger(__name__)

CODE_TTL_MINUTES = 10
RESEND_COOLDOWN_SECONDS = 60
MAX_ATTEMPTS = 5
MAX_SENDS_PER_HOUR = 5

_HTTP_STATUS = {
    "phone_in_use": 409,
    "resend_too_soon": 429,
    "too_many_attempts": 429,
    "invalid_phone": 400,
    "invalid_email": 400,
    "code_invalid": 400,
    "code_expired": 400,
    "sms_unavailable": 400,
    "email_unavailable": 400,
}


class VerificationError(ValueError):
    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


def http_status_for(code: str) -> int:
    return _HTTP_STATUS.get(code, 400)


def _hash_code(code: str, contact: str) -> str:
    return hashlib.sha256(f"{contact}:{code}".encode("utf-8")).hexdigest()


def _generate_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _parse_ts(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _recent_rows(admin: Any, *, column: str, value: str) -> list[dict[str, Any]]:
    since = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    rows = (
        admin.table("verification_codes")
        .select("created_at")
        .eq(column, value)
        .gte("created_at", since)
        .order("created_at", desc=True)
        .limit(MAX_SENDS_PER_HOUR + 1)
        .execute()
    )
    return list(rows.data or [])


def _assert_send_allowed(admin: Any, *, user_id: str, contact: str) -> None:
    now = datetime.now(timezone.utc)
    for column, value in (("user_id", user_id), ("phone_number", contact)):
        rows = _recent_rows(admin, column=column, value=value)
        if len(rows) >= MAX_SENDS_PER_HOUR:
            raise VerificationError(
                "Too many codes were requested. Try again in an hour.",
                "resend_too_soon",
            )
        if not rows:
            continue
        elapsed = (now - _parse_ts(rows[0]["created_at"])).total_seconds()
        if elapsed < RESEND_COOLDOWN_SECONDS:
            wait_for = int(RESEND_COOLDOWN_SECONDS - elapsed) or 1
            raise VerificationError(
                f"Please wait {wait_for}s before requesting another code.",
                "resend_too_soon",
            )


def issue_verification_code(
    *,
    user_id: str,
    contact: str,
    purpose: str,
    channel: str,
    target_type: str,
    target_id: str,
) -> str:
    """Store a hashed 6-digit code and return the plaintext for delivery."""
    contact = contact.strip()
    if not contact:
        code = "invalid_email" if purpose == "email" else "invalid_phone"
        raise VerificationError("A contact is required.", code)

    admin = get_supabase_admin()
    _assert_send_allowed(admin, user_id=user_id, contact=contact)
    code = _generate_code()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=CODE_TTL_MINUTES)
    admin.table("verification_codes").insert(
        {
            "user_id": user_id,
            "purpose": purpose,
            "target_type": target_type,
            "target_id": target_id,
            "phone_number": contact,
            "code_hash": _hash_code(code, contact),
            "channel": channel,
            "expires_at": expires_at.isoformat(),
            "max_attempts": MAX_ATTEMPTS,
        }
    ).execute()
    return code


def revoke_latest_code(*, user_id: str, purpose: str, contact: str) -> None:
    admin = get_supabase_admin()
    rows = (
        admin.table("verification_codes")
        .select("id")
        .eq("user_id", user_id)
        .eq("purpose", purpose)
        .eq("phone_number", contact)
        .is_("verified_at", None)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if rows.data:
        admin.table("verification_codes").delete().eq("id", rows.data[0]["id"]).execute()


def send_verification_code(
    *,
    user_id: str,
    phone_number: str,
    purpose: str,
    channel: str,
    target_type: str,
    target_id: str,
) -> None:
    """Generate and deliver a code. SMS failures raise `sms_unavailable`."""
    code = issue_verification_code(
        user_id=user_id,
        contact=phone_number,
        purpose=purpose,
        channel=channel,
        target_type=target_type,
        target_id=target_id,
    )
    message = f"Your Midora verification code is {code}. It expires in {CODE_TTL_MINUTES} minutes."
    from notifications.africastalking import send_sms, send_whatsapp_text

    if channel == "whatsapp":
        sent = send_whatsapp_text(phone_number, message)
    elif channel == "sms":
        sent = send_sms(phone_number, message)
    else:
        sent = True
    if channel == "sms" and not sent:
        revoke_latest_code(user_id=user_id, purpose=purpose, contact=phone_number)
        raise VerificationError(
            "Text messages are unavailable right now. Try again shortly.",
            "sms_unavailable",
        )
    if not sent:
        logger.warning(
            "Verification code for target=%s/%s purpose=%s could not be delivered via %s",
            target_type,
            target_id,
            purpose,
            channel,
        )


def confirm_verification_code(
    *,
    user_id: str,
    code: str,
    purpose: str,
    target_type: str,
    target_id: str,
    contact: str | None = None,
) -> str:
    """Validate `code` against the latest pending row. Returns the stored contact."""
    admin = get_supabase_admin()
    r = (
        admin.table("verification_codes")
        .select("*")
        .eq("user_id", user_id)
        .eq("target_type", target_type)
        .eq("target_id", target_id)
        .eq("purpose", purpose)
        .is_("verified_at", None)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if not r.data:
        raise VerificationError("That code is not valid. Request a new one.", "code_invalid")
    row = r.data[0]

    expires_at = row.get("expires_at")
    if expires_at and datetime.now(timezone.utc) > _parse_ts(expires_at):
        raise VerificationError("That code has expired. Request a new one.", "code_expired")

    attempts = int(row.get("attempts") or 0)
    max_attempts = int(row.get("max_attempts") or MAX_ATTEMPTS)
    if attempts >= max_attempts:
        raise VerificationError("Too many incorrect attempts. Request a new code.", "too_many_attempts")

    stored = str(row.get("phone_number") or "")
    if contact is not None and contact.strip() != stored:
        raise VerificationError("That code is not valid. Request a new one.", "code_invalid")

    expected_hash = str(row.get("code_hash") or "")
    if _hash_code(code.strip(), stored) != expected_hash:
        attempts += 1
        admin.table("verification_codes").update({"attempts": attempts}).eq("id", row["id"]).execute()
        if attempts >= max_attempts:
            raise VerificationError(
                "Too many incorrect attempts. Request a new code.",
                "too_many_attempts",
            )
        raise VerificationError("That code is not valid.", "code_invalid")

    admin.table("verification_codes").update(
        {"verified_at": datetime.now(timezone.utc).isoformat()}
    ).eq("id", row["id"]).execute()
    return stored
