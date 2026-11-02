from __future__ import annotations

import logging
import time
from typing import Any

_log = logging.getLogger(__name__)

QUALITY_WEIGHT = 40.0
FRESHNESS_WEIGHT = 30.0
TRUST_WEIGHT = 15.0
ENGAGEMENT_WEIGHT = 10.0
EXPOSURE_WEIGHT = 5.0
FRESHNESS_HALF_LIFE_HOURS = 72.0
FRAUD_MEDIUM_PENALTY = 20.0
FOLLOWED_SHOP_BOOST = 15.0

QUALITY_MIN = 40.0
EXPLORATION_MIN_QUALITY = 60.0
EXPLORATION_MAX_IMPRESSIONS = 50
PAID_MIN_QUALITY = 60.0

BLOCK_SIZE = 20
SELLER_CAP_PER_BLOCK = 2
PAID_SLOT_POSITION = 6
SPONSORED_SLOT_POSITION = 14
PAID_MIN_STAGE = 3
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

EXPOSURE_TARGET_IMPRESSIONS = 5000.0
EXPOSURE_MIN_MULTIPLIER = 0.4
EXPOSURE_WINDOW_HOURS = 24

FATIGUE_THRESHOLD = 3
FATIGUE_WINDOW_HOURS = 48

NEW_BADGE_HOURS = 72
CLOSING_SOON_HOURS = 168

TRENDING_WINDOW_HOURS = 168
TRENDING_MIN_UNIQUE_VIEWERS = 5
TRENDING_MIN_ACTIONS = 1
TRENDING_TOP_SHARE = 0.10

SNAPSHOT_TTL_SECONDS = 300
RANKING_FETCH_LIMIT = 1000
RANKED_MAX = 800

OVERRIDABLE_KEYS: tuple[str, ...] = (
    "QUALITY_WEIGHT",
    "FRESHNESS_WEIGHT",
    "TRUST_WEIGHT",
    "ENGAGEMENT_WEIGHT",
    "EXPOSURE_WEIGHT",
    "FRESHNESS_HALF_LIFE_HOURS",
    "FRAUD_MEDIUM_PENALTY",
    "FOLLOWED_SHOP_BOOST",
    "QUALITY_MIN",
    "EXPLORATION_MIN_QUALITY",
    "EXPLORATION_MAX_IMPRESSIONS",
    "PAID_MIN_QUALITY",
    "BLOCK_SIZE",
    "SELLER_CAP_PER_BLOCK",
    "PAID_SLOT_POSITION",
    "SPONSORED_SLOT_POSITION",
    "PAID_MIN_STAGE",
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
    "EXPOSURE_TARGET_IMPRESSIONS",
    "EXPOSURE_MIN_MULTIPLIER",
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
)

_DEFAULTS: dict[str, Any] = {k: globals()[k] for k in OVERRIDABLE_KEYS}
_CACHE_TTL_SECONDS = 60.0
_last_load_ts = 0.0
_current_overrides: dict[str, Any] = {}


def get_defaults() -> dict[str, Any]:
    return dict(_DEFAULTS)


def get_current_overrides() -> dict[str, Any]:
    return dict(_current_overrides)


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
        return raw if isinstance(raw, dict) else {}
    except Exception as exc:  # noqa: BLE001
        _log.debug("feed_config load failed: %s", exc)
        return {}


def refresh_from_db(force: bool = False) -> dict[str, Any]:
    global _last_load_ts
    now = time.time()
    if not force and now - _last_load_ts < _CACHE_TTL_SECONDS:
        return dict(_current_overrides)
    _last_load_ts = now
    return apply_overrides(_load_from_db())


def invalidate_cache() -> None:
    global _last_load_ts
    _last_load_ts = 0.0
