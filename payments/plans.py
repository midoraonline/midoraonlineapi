BASIC = "basic"
STANDARD = "standard"
PREMIUM = "premium"

PLAN_ORDER = [BASIC, STANDARD, PREMIUM]

PLANS = { 
        BASIC: {
            "max_photos": 3, "max_videos": 1, "free_boosts_per_month": 0, "reposts_per_day": 1, "repost_cooldown_hours": 24, "features": [], "is_active": True,
            "key": BASIC,
            "name": "Basic",
            "price_ugx": 0,
            "currency": "UGX",
            "billing_period_days": 30,
            "max_shops": 1,
            "max_products_per_shop": 10,
            "analytics_enabled": False,
            "frequency_per_day": 1,
            "frequency_per_day_boosted": 0, 
        },
        STANDARD: {
            "max_photos": 3, "max_videos": 1, "free_boosts_per_month": 2, "reposts_per_day": 3, "repost_cooldown_hours": 12, "features": [], "is_active": True,
            "key": STANDARD,
            "name": "Standard",
            "price_ugx": 30000,
            "currency": "UGX",
            "billing_period_days": 30,
            "max_shops": 3,
            "max_products_per_shop": 20,
            "analytics_enabled": True,
            "frequency_per_day": 0,
            "frequency_per_day_boosted": 2,
    
        },
        PREMIUM: {
            "max_photos": 3, "max_videos": 1, "free_boosts_per_month": 5, "reposts_per_day": 5, "repost_cooldown_hours": 6, "features": [], "is_active": True,
            "key": PREMIUM,
            "name": "Premium",
            "price_ugx": 80000,
            "currency": "UGX",
            "billing_period_days": 30,
            "max_shops": 10,
            "max_products_per_shop": 50,
            "analytics_enabled": True,
            "frequency_per_day": 0,
            "frequency_per_day_boosted": 5,
        },
}

DEFAULT_PLAN = BASIC


def is_valid_tier(tier: str | None) -> bool:
    return (tier or "").strip().lower() in PLANS


# --- Admin-editable storage (public.plans, migration 058). Code values above are the fallback.
import logging
import time

_log = logging.getLogger(__name__)
_TTL = 30.0
_cache: dict[str, dict] | None = None
_cache_at = 0.0

EDITABLE_FIELDS = {
    "name": str,
    "price_ugx": int,
    "currency": str,
    "billing_period_days": int,
    "max_shops": int,
    "max_products_per_shop": int,
    "analytics_enabled": bool,
    "frequency_per_day": int,
    "frequency_per_day_boosted": int,
    "max_photos": int,
    "max_videos": int,
    "free_boosts_per_month": int,
    "reposts_per_day": int,
    "repost_cooldown_hours": int,
    "features": list,
    "is_active": bool,
}


def invalidate_plans_cache() -> None:
    global _cache, _cache_at
    _cache = None
    _cache_at = 0.0


def _merged() -> dict[str, dict]:
    global _cache, _cache_at
    now = time.monotonic()
    if _cache is not None and now - _cache_at < _TTL:
        return _cache
    merged = {k: dict(v) for k, v in PLANS.items()}
    try:
        from db.supabase import get_supabase_admin

        rows = get_supabase_admin().table("plans").select("*").execute().data or []
        for row in rows:
            key = str(row.get("key") or "").lower()
            if key not in merged:
                continue
            for field in EDITABLE_FIELDS:
                if row.get(field) is not None:
                    merged[key][field] = row[field]
            if row.get("updated_at"):
                merged[key]["updated_at"] = row["updated_at"]
    except Exception as exc:  # noqa: BLE001 - table missing -> code defaults
        _log.debug("plans table unreadable, using code defaults: %s", exc)
    _cache, _cache_at = merged, now
    return merged


def get_plan(tier: str | None) -> dict:
    key = (tier or DEFAULT_PLAN).strip().lower()
    plans = _merged()
    return plans.get(key, plans[DEFAULT_PLAN])


def list_plans() -> list[dict]:
    plans = _merged()
    return [plans[key] for key in PLAN_ORDER]
