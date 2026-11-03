from __future__ import annotations

import logging
import time
from typing import Any

_log = logging.getLogger(__name__)

EARLY_QUALITY_WEIGHT = 40.0
EARLY_FRESHNESS_WEIGHT = 45.0
EARLY_EXPOSURE_WEIGHT = 15.0
MID_QUALITY_WEIGHT = 45.0
MID_FRESHNESS_WEIGHT = 35.0
MID_EXPOSURE_WEIGHT = 20.0
LATE_QUALITY_WEIGHT = 50.0
LATE_FRESHNESS_WEIGHT = 30.0
LATE_EXPOSURE_WEIGHT = 20.0
MVP_MID_MIN = 50
MVP_LATE_MIN = 100
LOCATION_WEIGHT_MID = 5.0
LOCATION_WEIGHT_PRODUCT = 15.0
LOCATION_WEIGHT_SERVICE = 25.0
LOCATION_WEIGHT_OPPORTUNITY = 15.0
SERVICE_FRESHNESS_STRETCH = 2.0
OPPORTUNITY_FRESHNESS_STRETCH = 1.5
TRUST_WEIGHT = 0.0
ENGAGEMENT_WEIGHT = 10.0
FRAUD_MEDIUM_PENALTY = 20.0
FOLLOWED_SHOP_BOOST = 15.0
FOLLOWED_SHOP_LIFT = 0

QUALITY_MIN = 40.0
FIRST_BLOCK_QUALITY_FLOOR = 65.0
EXPLORATION_MIN_QUALITY = 60.0
EXPLORATION_MAX_IMPRESSIONS = 50
PAID_MIN_QUALITY = 60.0

BLOCK_SIZE = 20
SELLER_CAP_PER_BLOCK = 2
MAX_SAME_TYPE_RUN = 4
MAX_SAME_SUBCATEGORY_RUN = 3
PAID_SLOT_POSITION = 6
SPONSORED_SLOT_POSITION = 14
PAID_MIN_STAGE = 3
TRENDING_MIN_STAGE = 3
FRESHNESS_RESET_COOLDOWN_HOURS = 72
TYPE_MIX_MIN_STAGE = 3
ENGAGEMENT_MIN_STAGE = 5
TYPE_DEPTH_BLOCKS = 3
PRODUCT_SHARE = 0.60
SERVICE_SHARE = 0.25
OPPORTUNITY_SHARE = 0.15

STAGE_1_MIN = 1
STAGE_2_MIN = 50
STAGE_3_MIN = 200
STAGE_4_MIN = 500
STAGE_5_MIN = 1000
STAGE_6_MIN = 5000
EXPLORATION_STAGE_3 = 1
EXPLORATION_STAGE_4 = 2

EXPOSURE_WINDOW_HOURS = 168

FATIGUE_THRESHOLD = 3
FATIGUE_WINDOW_HOURS = 48

NEW_BADGE_HOURS = 72
CLOSING_SOON_HOURS = 168

TRENDING_WINDOW_HOURS = 168
TRENDING_MIN_UNIQUE_VIEWERS = 5
TRENDING_MIN_ACTIONS = 1
TRENDING_TOP_SHARE = 0.10

SNAPSHOT_TTL_SECONDS = 300
FEED_SESSION_HOURS = 8
RECENTLY_SHOWN_HOURS = 24
RANKING_FETCH_LIMIT = 1000
RANKED_MAX = 800

OVERRIDABLE_KEYS: tuple[str, ...] = (
    "EARLY_QUALITY_WEIGHT",
    "EARLY_FRESHNESS_WEIGHT",
    "EARLY_EXPOSURE_WEIGHT",
    "MID_QUALITY_WEIGHT",
    "MID_FRESHNESS_WEIGHT",
    "MID_EXPOSURE_WEIGHT",
    "LATE_QUALITY_WEIGHT",
    "LATE_FRESHNESS_WEIGHT",
    "LATE_EXPOSURE_WEIGHT",
    "MVP_MID_MIN",
    "MVP_LATE_MIN",
    "LOCATION_WEIGHT_MID",
    "LOCATION_WEIGHT_PRODUCT",
    "LOCATION_WEIGHT_SERVICE",
    "LOCATION_WEIGHT_OPPORTUNITY",
    "SERVICE_FRESHNESS_STRETCH",
    "OPPORTUNITY_FRESHNESS_STRETCH",
    "TRUST_WEIGHT",
    "ENGAGEMENT_WEIGHT",
    "FRAUD_MEDIUM_PENALTY",
    "FOLLOWED_SHOP_BOOST",
    "FOLLOWED_SHOP_LIFT",
    "QUALITY_MIN",
    "FIRST_BLOCK_QUALITY_FLOOR",
    "EXPLORATION_MIN_QUALITY",
    "EXPLORATION_MAX_IMPRESSIONS",
    "PAID_MIN_QUALITY",
    "BLOCK_SIZE",
    "SELLER_CAP_PER_BLOCK",
    "MAX_SAME_TYPE_RUN",
    "MAX_SAME_SUBCATEGORY_RUN",
    "PAID_SLOT_POSITION",
    "SPONSORED_SLOT_POSITION",
    "PAID_MIN_STAGE",
    "TRENDING_MIN_STAGE",
    "FRESHNESS_RESET_COOLDOWN_HOURS",
    "TYPE_MIX_MIN_STAGE",
    "ENGAGEMENT_MIN_STAGE",
    "TYPE_DEPTH_BLOCKS",
    "PRODUCT_SHARE",
    "SERVICE_SHARE",
    "OPPORTUNITY_SHARE",
    "STAGE_1_MIN",
    "STAGE_2_MIN",
    "STAGE_3_MIN",
    "STAGE_4_MIN",
    "STAGE_5_MIN",
    "STAGE_6_MIN",
    "EXPLORATION_STAGE_3",
    "EXPLORATION_STAGE_4",
    "EXPOSURE_WINDOW_HOURS",
    "FATIGUE_THRESHOLD",
    "FATIGUE_WINDOW_HOURS",
    "NEW_BADGE_HOURS",
    "CLOSING_SOON_HOURS",
    "TRENDING_WINDOW_HOURS",
    "TRENDING_MIN_UNIQUE_VIEWERS",
    "TRENDING_MIN_ACTIONS",
    "TRENDING_TOP_SHARE",
    "SNAPSHOT_TTL_SECONDS",
    "FEED_SESSION_HOURS",
    "RECENTLY_SHOWN_HOURS",
)

_DEFAULTS: dict[str, Any] = {k: globals()[k] for k in OVERRIDABLE_KEYS}
_CACHE_TTL_SECONDS = 60.0
_SHARED_KEY = "midora:feed:config"
_last_load_ts = 0.0
_current_overrides: dict[str, Any] = {}


def get_defaults() -> dict[str, Any]:
    return dict(_DEFAULTS)


def get_current_overrides() -> dict[str, Any]:
    return dict(_current_overrides)


def version() -> str:
    import hashlib
    import json

    raw = json.dumps(_current_overrides, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode()).hexdigest()[:10]


def lane_shares() -> dict[str, float]:
    return {"product": PRODUCT_SHARE, "service": SERVICE_SHARE, "opportunity": OPPORTUNITY_SHARE}


def stage_minimums() -> tuple[int, ...]:
    return (STAGE_1_MIN, STAGE_2_MIN, STAGE_3_MIN, STAGE_4_MIN, STAGE_5_MIN, STAGE_6_MIN)


def _coerce(name: str, value: Any) -> Any:
    default = _DEFAULTS[name]
    try:
        if isinstance(default, int):
            return int(value)
        return float(value)
    except (TypeError, ValueError):
        return default


def apply_overrides(overrides: dict[str, Any]) -> dict[str, Any]:
    g = globals()
    applied: dict[str, Any] = {}
    for key in OVERRIDABLE_KEYS:
        if overrides.get(key) is not None:
            g[key] = applied[key] = _coerce(key, overrides[key])
        else:
            g[key] = _DEFAULTS[key]
    global _current_overrides
    _current_overrides = applied
    return applied


def _load_from_db() -> dict[str, Any]:
    from core import cache

    shared = cache.get_json(_SHARED_KEY)
    if isinstance(shared, dict):
        return shared
    try:
        from db.supabase import get_supabase_admin

        r = (
            get_supabase_admin()
            .table("feed_config")
            .select("overrides")
            .eq("key", "default")
            .maybe_single()
            .execute()
        )
        raw = (getattr(r, "data", None) or {}).get("overrides") or {}
        raw = raw if isinstance(raw, dict) else {}
        cache.set_json(_SHARED_KEY, raw, ttl_seconds=60)
        return raw
    except Exception as exc:  # noqa: BLE001
        _log.debug("feed_config load failed: %s", exc)
        return {}


def refresh_from_db(force: bool = False) -> dict[str, Any]:
    global _last_load_ts
    from core import cache

    now = time.time()
    ttl = 5.0 if cache.get_redis() is not None else _CACHE_TTL_SECONDS
    if force:
        cache.delete(_SHARED_KEY)
    if not force and now - _last_load_ts < ttl:
        return dict(_current_overrides)
    _last_load_ts = now
    return apply_overrides(_load_from_db())


def invalidate_cache() -> None:
    global _last_load_ts
    _last_load_ts = 0.0
    from core import cache

    cache.delete(_SHARED_KEY)
