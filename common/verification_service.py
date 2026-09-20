"""Generic OTP verification codes — shared by phone (SMS) and WhatsApp flows.

One `verification_codes` table backs both: `target_type`/`target_id` point at
either a user (their own phone_number) or a shop (its whatsapp_number), and
`purpose` says which contact channel is being proven. Callers own the actual
column updates (users.phone_verified / shops.whatsapp_verified) — this module
only manages the OTP lifecycle.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

from db.supabase import get_supabase_admin

logger = logging.getLogger(__name__)

CODE_TTL_MINUTES = 10
RESEND_COOLDOWN_SECONDS = 60
MAX_ATTEMPTS = 5


def _hash_code(code: str, phone_number: str) -> str:
    return hashlib.sha256(f"{phone_number}:{code}".encode("utf-8")).hexdigest()


def _generate_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def send_verification_code(
    *,
    user_id: str,
    phone_number: str,
    purpose: str,
    channel: str,
    target_type: str,
    target_id: str,
) -> None:
    """Generate + deliver a 6-digit OTP. Raises `ValueError` on validation/rate-limit failures."""
    phone_number = phone_number.strip()
    if not phone_number:
        raise ValueError("Phone number is required")

    admin = get_supabase_admin()

    recent = (
        admin.table("verification_codes")
        .select("created_at")
        .eq("target_type", target_type)
        .eq("target_id", target_id)
        .eq("purpose", purpose)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if recent.data:
        last_created = recent.data[0].get("created_at")
        if last_created:
            elapsed = (datetime.now(timezone.utc) - _parse_ts(last_created)).total_seconds()
            if elapsed < RESEND_COOLDOWN_SECONDS:
                wait_for = int(RESEND_COOLDOWN_SECONDS - elapsed)
                raise ValueError(f"Please wait {wait_for}s before requesting another code")

    code = _generate_code()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=CODE_TTL_MINUTES)
    admin.table("verification_codes").insert(
        {
            "user_id": user_id,
            "purpose": purpose,
            "target_type": target_type,
            "target_id": target_id,
            "phone_number": phone_number,
            "code_hash": _hash_code(code, phone_number),
            "channel": channel,
            "expires_at": expires_at.isoformat(),
            "max_attempts": MAX_ATTEMPTS,
        }
    ).execute()

    message = f"Your Midora verification code is {code}. It expires in {CODE_TTL_MINUTES} minutes."
    from notifications.africastalking import send_sms, send_whatsapp_text

    sent = send_whatsapp_text(phone_number, message) if channel == "whatsapp" else send_sms(phone_number, message)
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
) -> str:
    """Validate `code` against the latest pending row. Returns the verified phone_number."""
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
        raise ValueError("No pending verification. Please request a new code.")
    row = r.data[0]

    expires_at = row.get("expires_at")
    if expires_at and datetime.now(timezone.utc) > _parse_ts(expires_at):
        raise ValueError("Verification code expired. Please request a new one.")

    attempts = int(row.get("attempts") or 0)
    if attempts >= int(row.get("max_attempts") or MAX_ATTEMPTS):
        raise ValueError("Too many incorrect attempts. Please request a new code.")

    phone_number = str(row.get("phone_number") or "")
    expected_hash = str(row.get("code_hash") or "")
    if _hash_code(code.strip(), phone_number) != expected_hash:
        admin.table("verification_codes").update({"attempts": attempts + 1}).eq("id", row["id"]).execute()
        raise ValueError("Incorrect verification code")

    admin.table("verification_codes").update(
        {"verified_at": datetime.now(timezone.utc).isoformat()}
    ).eq("id", row["id"]).execute()
    return phone_number
