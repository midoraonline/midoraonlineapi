"""Whether a user has verified a phone or email and may post a listing."""

from __future__ import annotations

from typing import Any


def contact_verification_status(row: dict[str, Any] | None) -> dict[str, Any]:
    data = row or {}
    phone = (data.get("phone_number") or "").strip() or None
    email = (data.get("email") or "").strip() or None
    phone_verified = bool(data.get("phone_verified"))
    email_verified = bool(data.get("email_verified"))
    can_post = phone_verified or email_verified
    if can_post:
        required_channel = None
    elif phone:
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
