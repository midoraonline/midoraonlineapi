"""Theme and notification preferences stored on users.preferences."""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = logging.getLogger(__name__)

Theme = Literal["light", "dark", "system"]
NOTIFICATION_KINDS = (
    "messages",
    "listing_approved",
    "listing_rejected",
    "reviews",
    "reports",
)


class NotificationPreferences(BaseModel):
    model_config = ConfigDict(extra="ignore")

    push: bool = True
    email: bool = True
    messages: bool = True
    listing_approved: bool = True
    listing_rejected: bool = True
    reviews: bool = True
    reports: bool = True


class UserPreferences(BaseModel):
    model_config = ConfigDict(extra="ignore")

    theme: Theme = "system"
    notifications: NotificationPreferences = Field(default_factory=NotificationPreferences)

    @field_validator("theme", mode="before")
    @classmethod
    def _theme(cls, value: object) -> str:
        text = str(value or "system").strip().lower()
        if text not in ("light", "dark", "system"):
            raise ValueError("theme must be light, dark, or system")
        return text


DEFAULT_PREFERENCES = UserPreferences().model_dump()


class NotificationPreferencesPatch(BaseModel):
    model_config = ConfigDict(extra="ignore")

    push: bool | None = None
    email: bool | None = None
    messages: bool | None = None
    listing_approved: bool | None = None
    listing_rejected: bool | None = None
    reviews: bool | None = None
    reports: bool | None = None


class UserPreferencesPatch(BaseModel):
    model_config = ConfigDict(extra="ignore")

    theme: Theme | None = None
    notifications: NotificationPreferencesPatch | None = None

    @field_validator("theme", mode="before")
    @classmethod
    def _theme(cls, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip().lower()
        if text not in ("light", "dark", "system"):
            raise ValueError("theme must be light, dark, or system")
        return text


def coerce_preferences(value: Any) -> dict[str, Any]:
    """Full object for clients. Unknown or partial JSON keeps the defaults."""
    if not isinstance(value, dict):
        return dict(DEFAULT_PREFERENCES)
    try:
        return UserPreferences.model_validate(value).model_dump()
    except Exception:
        return dict(DEFAULT_PREFERENCES)


def merge_preferences(current: Any, patch: UserPreferencesPatch) -> dict[str, Any]:
    base = coerce_preferences(current)
    if patch.theme is not None:
        base["theme"] = patch.theme
    if patch.notifications is not None:
        sent = patch.notifications.model_dump(exclude_unset=True)
        base["notifications"].update({key: value for key, value in sent.items() if value is not None})
    return UserPreferences.model_validate(base).model_dump()


def load_preferences(user_id: str) -> dict[str, Any]:
    """Read stored preferences. Missing column or row uses defaults (toggles on)."""
    from db.supabase import get_supabase_admin

    try:
        result = (
            get_supabase_admin()
            .table("users")
            .select("preferences")
            .eq("id", user_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        logger.info("preferences read skipped for %s: %s", user_id, exc)
        return dict(DEFAULT_PREFERENCES)
    if not result.data:
        return dict(DEFAULT_PREFERENCES)
    return coerce_preferences(result.data[0].get("preferences"))


def notification_enabled(user_id: str, kind: str, channel: str) -> bool:
    """Type toggle gates every channel. push/email masters gate those channels only."""
    prefs = load_preferences(user_id)
    notes = prefs.get("notifications") or {}
    if kind in NOTIFICATION_KINDS and notes.get(kind) is False:
        return False
    if channel == "push" and notes.get("push") is False:
        return False
    if channel == "email" and notes.get("email") is False:
        return False
    return True


def emails_allowed(addresses: list[str], kind: str) -> list[str]:
    """Drop addresses whose user turned this kind or email off. Unknown addresses stay."""
    cleaned = [item.strip() for item in addresses if item and item.strip()]
    if not cleaned:
        return []
    from db.supabase import get_supabase_admin

    try:
        result = (
            get_supabase_admin()
            .table("users")
            .select("email,preferences")
            .in_("email", cleaned)
            .execute()
        )
    except Exception as exc:
        logger.info("preferences email filter skipped: %s", exc)
        return cleaned
    blocked: set[str] = set()
    for row in result.data or []:
        email = str(row.get("email") or "").strip().lower()
        prefs = coerce_preferences(row.get("preferences"))
        notes = prefs.get("notifications") or {}
        if notes.get("email") is False or (kind in NOTIFICATION_KINDS and notes.get(kind) is False):
            blocked.add(email)
    return [item for item in cleaned if item.lower() not in blocked]
