"""Whether a user has verified a phone or email and may post a listing."""

from __future__ import annotations

from typing import Any


def contact_verification_status(
    row: dict[str, Any] | None,
    *,
    require_phone: bool | None = None,
    require_email: bool | None = None,
) -> dict[str, Any]:
    from platform_settings import flags

    if require_phone is None:
        require_phone = flags.phone_verification_required()
    if require_email is None:
        require_email = flags.email_verification_required()
    data = row or {}
    phone = (data.get("phone_number") or "").strip() or None
    email = (data.get("email") or "").strip() or None
    phone_verified = bool(data.get("phone_verified"))
    email_verified = bool(data.get("email_verified"))
    phone_missing = require_phone and not phone_verified
    email_missing = require_email and not email_verified
    can_post = not phone_missing and not email_missing
    if can_post:
        required_channel = None
    elif phone_missing:
        required_channel = "phone"
    else:
        required_channel = "email"
    return {
        "phone": phone,
        "phone_verified": phone_verified,
        "email": email,
        "email_verified": email_verified,
        "can_post": can_post,
        "required_channel": required_channel,
    }
