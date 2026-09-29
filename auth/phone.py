"""E.164 phone numbers. Uganda (+256) is the default when the country is omitted."""

from __future__ import annotations

import re

_E164 = re.compile(r"^\+[1-9]\d{7,14}$")
_SEPARATORS = re.compile(r"[\s\-().]")


class PhoneError(ValueError):
    def __init__(self, message: str, code: str = "invalid_phone") -> None:
        super().__init__(message)
        self.code = code


def normalize_phone(raw: str | None, *, default_country: str = "256") -> str:
    text = _SEPARATORS.sub("", (raw or "").strip())
    if not text:
        raise PhoneError("Enter a phone number.")
    if text.startswith("00"):
        text = "+" + text[2:]
    elif text.startswith("+"):
        pass
    elif text.startswith(default_country):
        text = "+" + text
    elif text.startswith("0"):
        text = f"+{default_country}" + text[1:]
    else:
        text = f"+{default_country}" + text
    if not _E164.match(text):
        raise PhoneError("Enter a valid phone number.")
    return text


def phone_lookup_forms(e164: str) -> list[str]:
    """Forms already stored on older rows, plus the E.164 value."""
    forms = [e164]
    if e164.startswith("+256") and len(e164) == 13:
        national = e164[4:]
        forms.extend([f"0{national}", f"256{national}", national])
    return forms
