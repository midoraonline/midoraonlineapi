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
        "description": "Maximum photos and videos per listing, per plan. Value: {default: {max_photos, max_videos}, plans: {basic|standard|premium: {...}}}.",
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


def _plan_keys() -> list[str]:
    from payments.plans import PLAN_ORDER

    return list(PLAN_ORDER)


def _pair(raw: Any, fallback: dict[str, int]) -> dict[str, int]:
    raw = raw if isinstance(raw, dict) else {}
    return {
        "max_photos": _clamp_int(raw.get("max_photos"), fallback["max_photos"], 1, MAX_PHOTOS_CEILING),
        "max_videos": _clamp_int(raw.get("max_videos"), fallback["max_videos"], 0, MAX_VIDEOS_CEILING),
    }


def all_media_limits() -> dict[str, Any]:
    """{"default": {...}, "plans": {tier: {...}}}. Older flat values act as the default."""
    try:
        row = load_settings().get("listing_media_limits") or {}
    except Exception:  # noqa: BLE001
        row = {}
    value = row.get("value") if isinstance(row.get("value"), dict) else {}
    base = {"max_photos": DEFAULT_MAX_PHOTOS, "max_videos": DEFAULT_MAX_VIDEOS}
    default = _pair(value.get("default") if isinstance(value.get("default"), dict) else value, base)
    stored = value.get("plans") if isinstance(value.get("plans"), dict) else {}
    plans = {tier: _pair(stored.get(tier), default) for tier in _plan_keys()}
    return {"default": default, "plans": plans}


def media_limits(plan_tier: str | None = None) -> dict[str, int]:
    """Caps for one plan; unknown/missing plan falls back to the default (3 photos / 1 video)."""
    data = all_media_limits()
    tier = (plan_tier or "").strip().lower()
    return dict(data["plans"].get(tier) or data["default"])


def _check_pair(raw: Any, where: str) -> dict[str, int]:
    if not isinstance(raw, dict):
        raise HTTPException(status_code=422, detail={"detail": f"{where} must be an object.", "code": "invalid_limit"})
    out = {}
    for key, lo, hi in (("max_photos", 1, MAX_PHOTOS_CEILING), ("max_videos", 0, MAX_VIDEOS_CEILING)):
        if key not in raw:
            continue
        try:
            n = int(raw[key])
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail={"detail": f"{where} {key} must be a whole number.", "code": "invalid_limit"})
        if n < lo or n > hi:
            raise HTTPException(status_code=422, detail={"detail": f"{where} {key} must be between {lo} and {hi}.", "code": "invalid_limit"})
        out[key] = n
    return out


def normalize_media_limits_value(value: dict[str, Any]) -> dict[str, Any]:
    """Accepts {default, plans:{tier:{...}}} or the older flat {max_photos, max_videos}."""
    current = all_media_limits()
    if "default" not in value and "plans" not in value:
        value = {"default": value}
    default = {**current["default"], **_check_pair(value.get("default", {}), "default")}
    plans_in = value.get("plans") or {}
    if not isinstance(plans_in, dict):
        raise HTTPException(status_code=422, detail={"detail": "plans must be an object.", "code": "invalid_limit"})
    unknown = [k for k in plans_in if k not in _plan_keys()]
    if unknown:
        raise HTTPException(status_code=422, detail={"detail": f"Unknown plan: {unknown[0]}.", "code": "invalid_limit"})
    plans = {}
    for tier in _plan_keys():
        plans[tier] = {**current["plans"][tier], **_check_pair(plans_in.get(tier, {}), tier)}
    return {"default": default, "plans": plans}


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
