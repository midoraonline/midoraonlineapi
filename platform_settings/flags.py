"""Platform feature switches stored in app_settings.

Defaults apply when the table is missing or a key has not been seeded, so a
skipped migration does not 500. Analytics defaults off.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import HTTPException

logger = logging.getLogger(__name__)

_TTL_SECONDS = 30.0

FLAGS: dict[str, dict[str, Any]] = {
    "analytics": {
        "default": False,
        "public": True,
        "description": "Record shop and listing analytics. When off, events are not stored and analytics responses say disabled.",
    },
    "analytics_all_plans": {
        "default": False,
        "public": True,
        "description": "Give every subscription plan, including Basic, access to shop analytics. Needs Analytics on.",
    },
    "signups_allowed": {
        "default": True,
        "public": True,
        "description": "Allow new email and Google accounts. Existing users can still sign in.",
    },
    "listings_require_review": {
        "default": False,
        "public": True,
        "description": "New listings stay in manual review. Auto-approve from the moderation pipeline is turned off.",
    },
    "ai_moderation": {
        "default": True,
        "public": True,
        "description": "Run Gemini and OpenAI moderation. Keyword, image-hash, and profanity checks still run when this is off.",
    },
    "maintenance_mode": {
        "default": False,
        "public": True,
        "description": "Pause new shops, new listings, and listing edits. Marking a listing sold or closed still works.",
    },
    "listing_media_limits": {
        "default": True,
        "public": False,
        "kind": "limits",
        "description": "Maximum photos and videos per listing. Value: {max_photos, max_videos}.",
    },
}

DEFAULT_MAX_PHOTOS = 3
DEFAULT_MAX_VIDEOS = 1
MAX_PHOTOS_CEILING = 20
MAX_VIDEOS_CEILING = 5


def _clamp_int(raw: Any, default: int, lo: int, hi: int) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def media_limits() -> dict[str, int]:
    """Admin-configured per-listing media caps (defaults: 3 photos, 1 video)."""
    try:
        row = load_settings().get("listing_media_limits") or {}
    except Exception:  # noqa: BLE001
        row = {}
    value = row.get("value") if isinstance(row.get("value"), dict) else {}
    return {
        "max_photos": _clamp_int(value.get("max_photos"), DEFAULT_MAX_PHOTOS, 1, MAX_PHOTOS_CEILING),
        "max_videos": _clamp_int(value.get("max_videos"), DEFAULT_MAX_VIDEOS, 0, MAX_VIDEOS_CEILING),
    }


def normalize_media_limits_value(value: dict[str, Any]) -> dict[str, int]:
    for key, lo, hi in (("max_photos", 1, MAX_PHOTOS_CEILING), ("max_videos", 0, MAX_VIDEOS_CEILING)):
        if key in value:
            try:
                n = int(value[key])
            except (TypeError, ValueError):
                raise HTTPException(status_code=422, detail={"detail": f"{key} must be a whole number.", "code": "invalid_limit"})
            if n < lo or n > hi:
                raise HTTPException(status_code=422, detail={"detail": f"{key} must be between {lo} and {hi}.", "code": "invalid_limit"})
    current = media_limits()
    return {
        "max_photos": int(value.get("max_photos", current["max_photos"])),
        "max_videos": int(value.get("max_videos", current["max_videos"])),
    }

_cache_rows: dict[str, dict[str, Any]] | None = None
_cache_at: float = 0.0
_cache_persisted: bool = False


class SignupsClosed(ValueError):
    pass


def invalidate_cache() -> None:
    global _cache_rows, _cache_at, _cache_persisted
    _cache_rows = None
    _cache_at = 0.0
    _cache_persisted = False


def _defaults() -> dict[str, dict[str, Any]]:
    return {
        key: {"key": key, "enabled": bool(spec["default"]), "value": {}, "updated_by": None, "updated_at": None}
        for key, spec in FLAGS.items()
    }


def _load_rows() -> list[dict[str, Any]]:
    from db.supabase import get_supabase_admin

    admin = get_supabase_admin()
    return admin.table("app_settings").select("key,enabled,value,updated_by,updated_at").execute().data or []


def load_settings(*, force: bool = False) -> dict[str, dict[str, Any]]:
    global _cache_rows, _cache_at, _cache_persisted
    now = time.monotonic()
    if not force and _cache_rows is not None and (now - _cache_at) < _TTL_SECONDS:
        return _cache_rows

    merged = _defaults()
    persisted = False
    try:
        for row in _load_rows():
            key = str(row.get("key") or "")
            if key not in FLAGS:
                continue
            value = row.get("value") if isinstance(row.get("value"), dict) else {}
            merged[key] = {
                "key": key,
                "enabled": bool(row.get("enabled")),
                "value": value,
                "updated_by": row.get("updated_by"),
                "updated_at": row.get("updated_at"),
            }
        persisted = True
    except Exception as exc:
        logger.warning("app_settings unreadable, using defaults: %s", exc)
        merged = _defaults()
        persisted = False
    _cache_rows = merged
    _cache_at = now
    _cache_persisted = persisted
    return merged


def enabled(key: str) -> bool:
    spec = FLAGS.get(key)
    if spec is None:
        return False
    row = load_settings().get(key)
    if row is None:
        return bool(spec["default"])
    return bool(row["enabled"])


def analytics_enabled() -> bool:
    return enabled("analytics")


def analytics_all_plans() -> bool:
    return enabled("analytics_all_plans")


def signups_allowed() -> bool:
    return enabled("signups_allowed")


def listings_require_review() -> bool:
    return enabled("listings_require_review")


def ai_moderation_enabled() -> bool:
    return enabled("ai_moderation")


def public_flags() -> dict[str, bool]:
    rows = load_settings()
    return {key: bool(rows[key]["enabled"]) for key, spec in FLAGS.items() if spec["public"]}


def admin_items() -> list[dict[str, Any]]:
    rows = load_settings(force=True)
    items = []
    for key, spec in FLAGS.items():
        row = rows[key]
        items.append(
            {
                "key": key,
                "enabled": bool(row["enabled"]),
                "value": row.get("value") or {},
                "updated_by": row.get("updated_by"),
                "updated_at": row.get("updated_at"),
                "description": spec["description"],
                "public": bool(spec["public"]),
                "kind": spec.get("kind", "switch"),
            }
        )
    return items


def settings_persisted() -> bool:
    load_settings()
    return _cache_persisted


def assert_posting_open() -> None:
    if enabled("maintenance_mode"):
        raise HTTPException(
            status_code=503,
            detail={
                "detail": "Posting is paused for maintenance.",
                "code": "maintenance_mode",
            },
        )


def assert_signups_open() -> None:
    if not signups_allowed():
        raise SignupsClosed("New signups are closed.")
