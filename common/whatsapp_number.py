"""Shop WhatsApp numbers: any country, stored as E.164, Uganda when the country is not given."""

from __future__ import annotations

import re

import phonenumbers
from phonenumbers import NumberParseException, PhoneNumberFormat

DEFAULT_REGION = "UG"
_SEPARATORS = re.compile(r"[\s\-().]")
_REGION = re.compile(r"^[A-Za-z]{2}$")


class WhatsAppNumberError(ValueError):
    def __init__(self, message: str, code: str = "invalid_whatsapp_number") -> None:
        super().__init__(message)
        self.code = code


def _region(value: str | None) -> str:
    text = (value or "").strip()
    return text.upper() if _REGION.match(text) else DEFAULT_REGION


def _valid_e164(candidate: str, region: str | None) -> str | None:
    try:
        parsed = phonenumbers.parse(candidate, region)
    except NumberParseException:
        return None
    if not phonenumbers.is_valid_number(parsed):
        return None
    return phonenumbers.format_number(parsed, PhoneNumberFormat.E164)


def normalize_whatsapp(raw: str | None, region: str | None = None) -> str:
    """Return +E.164 or raise WhatsAppNumberError. `region` is an ISO-3166 alpha-2 code."""
    text = _SEPARATORS.sub("", (raw or "").strip())
    if not text:
        raise WhatsAppNumberError("Enter a WhatsApp number.", "whatsapp_required")
    if text.startswith("00"):
        text = "+" + text[2:]
    iso = _region(region)
    found = _valid_e164(text, None if text.startswith("+") else iso)
    if found is None and not text.startswith("+") and text.isdigit() and len(text) >= 10:
        found = _valid_e164("+" + text, None)  # country code typed without the +
    if found is None:
        raise WhatsAppNumberError(
            "That doesn't look like a valid phone number for the selected country.",
        )
    return found


def link_digits(e164: str) -> str:
    """Digits for https://wa.me/<digits>."""
    return re.sub(r"\D", "", e164 or "")


def region_of(e164: str) -> str | None:
    try:
        return phonenumbers.region_code_for_number(phonenumbers.parse(e164, None))
    except NumberParseException:
        return None
