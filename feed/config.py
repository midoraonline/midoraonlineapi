"""Feed scoring & placement tunables.

Single source of truth for the Midora feed engine. All bonuses, penalties,
freshness curves and composition targets live here so scoring, placement
and admin surfaces read the same numbers.

Adjust these values (or wire them to an admin `feed_config` table later)
without touching scoring/placement logic.
"""

from __future__ import annotations

from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Score bonuses (positive)
# ---------------------------------------------------------------------------
VECTOR_SCORE_SCALE = 80.0          # cosine_similarity * 80 = Taste Match
CATEGORY_MATCH_BOOST = 30.0        # user's preferred categories
SEARCH_MATCH_PER_MATCH = 20.0      # +20 per matched search term
SEARCH_MATCH_MAX_HITS = 2          # cap = 2 matches -> +40 max
FOLLOWED_SHOP_BOOST = 50.0         # buyer-seller relationship
NEW_SELLER_BONUS = 30.0            # cold-start protection
EXPLORATION_BONUS = 20.0           # slot-based, applied by placement engine
SELLER_QUALITY_CAP = 25.0          # response rate, verified, low disputes
GLOBAL_POPULARITY_CAP = 30.0       # marketplace demand (aggregate)
PREMIUM_STORE_BONUS = 15.0         # active subscription
SPONSORED_LISTING_BONUS = 25.0     # guaranteed exposure tier
LISTING_BOOST_BONUS = 50.0         # paid seller boost (regular)
SUPER_BOOST_BONUS = 75.0           # short-duration premium boost


# ---------------------------------------------------------------------------
# Freshness curve — hours -> bonus points
# ---------------------------------------------------------------------------
# (max_age_hours, bonus).  First matching bucket wins.
FRESHNESS_CURVE: tuple[tuple[float, float], ...] = (
    (24.0, 40.0),         # 0-24h   -> +40
    (24.0 * 3, 30.0),     # 1-3d    -> +30
    (24.0 * 7, 20.0),     # 3-7d    -> +20
    (24.0 * 14, 10.0),    # 7-14d   -> +10
)
FRESHNESS_FALLBACK = 0.0             # 14d+


# ---------------------------------------------------------------------------
# Velocity — recent (short-window) engagement
# ---------------------------------------------------------------------------
VELOCITY_WINDOW_HOURS = 48
VELOCITY_MAX_BONUS = 40.0
# Per-event weights fed into a saturating curve
VELOCITY_WEIGHTS: dict[str, float] = {
    "viewed": 1.0,
    "whatsapp_clicked": 3.0,
    "messaged": 4.0,
    "saved": 3.0,
    "shared": 2.0,
    "like": 3.0,
}
# Score = MAX_BONUS * (1 - exp(-raw / VELOCITY_HALF_LIFE))
VELOCITY_HALF_LIFE = 20.0


# ---------------------------------------------------------------------------
# New seller cold-start
# ---------------------------------------------------------------------------
NEW_SELLER_MAX_AGE_DAYS = 30
NEW_SELLER_MAX_IMPRESSIONS = 500
NEW_SELLER_GUARANTEED_IMPRESSIONS = 500  # tracked externally


# ---------------------------------------------------------------------------
# Penalties (negative)
# ---------------------------------------------------------------------------
SEEN_DEMOTION = 50.0              # user already interacted with this listing

# Fraud severity -> penalty. `critical` returns None -> product filtered out.
FRAUD_SEVERITY_PENALTY: dict[str, float | None] = {
    "low": 0.0,
    "medium": -50.0,
    "high": -200.0,
    "critical": None,             # None -> hide from feed entirely
}


# ---------------------------------------------------------------------------
# Vendor diversity — POST-ranking placement constraint
# ---------------------------------------------------------------------------
# Progressive penalty applied during placement (not scoring):
#     penalty = min(VENDOR_DIVERSITY_CAP, K * (rank_in_feed - 1)^2)
VENDOR_DIVERSITY_K = 5.0
VENDOR_DIVERSITY_CAP = 75.0
# Hard cap: no seller may occupy more than N positions inside any W-position
# sliding window.
VENDOR_WINDOW_SIZE = 12
VENDOR_WINDOW_MAX_PER_SELLER = 2


# ---------------------------------------------------------------------------
# Feed composition targets (per 100 positions)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SlotCadence:
    """`one_per_n` = insert one item of this kind every N positions.

    `phase` staggers the starting position to avoid collisions between
    reserved slots (higher-priority kinds are placed first when two
    cadences collide on the same position).
    """
    kind: str
    one_per_n: int
    phase: int = 0
    priority: int = 0


# NOTE: `organic` is the residual pool; the cadences below reserve slots that
# are pulled from other pools and inserted at deterministic positions.
# Priority ties (same position) are broken by higher `priority` first.
COMPOSITION_CADENCE: tuple[SlotCadence, ...] = (
    SlotCadence("super_boost",  25, phase=25, priority=6),  # ~4 per 100 (highest)
    SlotCadence("sponsored",     8, phase=4,  priority=5),  # ~12 per 100
    SlotCadence("boosted",       8, phase=8,  priority=4),  # ~12 per 100
    SlotCadence("premium_store", 14, phase=14, priority=3), # ~7 per 100
    SlotCadence("fresh",         16, phase=6,  priority=2), # ~6 per 100 — new sellers / new inventory
    SlotCadence("exploration",   25, phase=13, priority=1), # ~4 per 100 — off-profile discovery
)


# ---------------------------------------------------------------------------
# Candidate pool sizing
# ---------------------------------------------------------------------------
CANDIDATE_POOL_MAX = 500
FRESHNESS_MAX_AGE_DAYS = 14      # any listing newer than this can earn freshness

# Implicit-feedback weights for the user preference vector
INTERACTION_WEIGHTS: dict[str, float] = {
    "like": 5.0,
    "view": 1.0,
    "save": 4.0,
    "whatsapp": 3.0,
    "message": 4.0,
}
DECAY_LAMBDA_PER_DAY = 0.01


# ---------------------------------------------------------------------------
# Exposure multiplier — post-impression marketplace diversity control
# ---------------------------------------------------------------------------
# Applied as a fractional multiplier to the pre-placement score:
#     multiplier = 1 / (1 + (impressions_24h / TARGET))^EXPONENT
# capped to [MIN, 1.0]. A shop that already got 5k impressions today keeps
# getting scored but is gently rotated off the top so smaller shops surface.
EXPOSURE_TARGET_IMPRESSIONS = 5000.0
EXPOSURE_EXPONENT = 1.0
EXPOSURE_MIN_MULTIPLIER = 0.4
EXPOSURE_WINDOW_HOURS = 24


# ---------------------------------------------------------------------------
# Impression fatigue — hide listings already shown too many times
# ---------------------------------------------------------------------------
FATIGUE_THRESHOLD = 3          # >= this many impressions -> hide
FATIGUE_WINDOW_HOURS = 48
# Soft de-dup window: hide *any* listing this viewer saw in the last N hours
# (drives the "pagination without repeats" behaviour across reloads).
DEDUP_WINDOW_HOURS = 24
DEDUP_MAX_LOOKBACK = 500


# ---------------------------------------------------------------------------
# Admin overrides — merged from `feed_config` table
# ---------------------------------------------------------------------------
# Whitelist of scalar tunables that admins are allowed to override at
# runtime. Anything outside this list is silently dropped when overrides
# are applied so misconfiguration cannot inject unexpected fields.
import logging as _logging
import time as _time
from typing import Any as _Any

_log = _logging.getLogger(__name__)

OVERRIDABLE_KEYS: tuple[str, ...] = (
    "VECTOR_SCORE_SCALE",
    "CATEGORY_MATCH_BOOST",
    "SEARCH_MATCH_PER_MATCH",
    "SEARCH_MATCH_MAX_HITS",
    "FOLLOWED_SHOP_BOOST",
    "NEW_SELLER_BONUS",
    "EXPLORATION_BONUS",
    "SELLER_QUALITY_CAP",
    "GLOBAL_POPULARITY_CAP",
    "PREMIUM_STORE_BONUS",
    "SPONSORED_LISTING_BONUS",
    "LISTING_BOOST_BONUS",
    "SUPER_BOOST_BONUS",
    "FRESHNESS_FALLBACK",
    "VELOCITY_WINDOW_HOURS",
    "VELOCITY_MAX_BONUS",
    "VELOCITY_HALF_LIFE",
    "NEW_SELLER_MAX_AGE_DAYS",
    "NEW_SELLER_MAX_IMPRESSIONS",
    "NEW_SELLER_GUARANTEED_IMPRESSIONS",
    "SEEN_DEMOTION",
    "VENDOR_DIVERSITY_K",
    "VENDOR_DIVERSITY_CAP",
    "VENDOR_WINDOW_SIZE",
    "VENDOR_WINDOW_MAX_PER_SELLER",
    "CANDIDATE_POOL_MAX",
    "FRESHNESS_MAX_AGE_DAYS",
    "DECAY_LAMBDA_PER_DAY",
    "EXPOSURE_TARGET_IMPRESSIONS",
    "EXPOSURE_EXPONENT",
    "EXPOSURE_MIN_MULTIPLIER",
    "EXPOSURE_WINDOW_HOURS",
    "FATIGUE_THRESHOLD",
    "FATIGUE_WINDOW_HOURS",
    "DEDUP_WINDOW_HOURS",
    "DEDUP_MAX_LOOKBACK",
)

# Snapshot of the ORIGINAL module defaults so admins can reset / see baseline.
_DEFAULTS: dict[str, _Any] = {}

_CACHE_TTL_SECONDS = 60.0
_last_load_ts: float = 0.0
_current_overrides: dict[str, _Any] = {}


def _snapshot_defaults() -> None:
    if _DEFAULTS:
        return
    g = globals()
    for k in OVERRIDABLE_KEYS:
        if k in g:
            _DEFAULTS[k] = g[k]


_snapshot_defaults()


def get_defaults() -> dict[str, _Any]:
    """Return the code-level defaults for every overridable key."""
    _snapshot_defaults()
    return dict(_DEFAULTS)


def get_current_overrides() -> dict[str, _Any]:
    """Return the currently-applied override map (subset of OVERRIDABLE_KEYS)."""
    return dict(_current_overrides)


def _coerce(name: str, value: _Any) -> _Any:
    """Best-effort cast override values into the type of the default."""
    default = _DEFAULTS.get(name)
    if default is None:
        return value
    try:
        if isinstance(default, bool):
            return bool(value)
        if isinstance(default, int) and not isinstance(default, bool):
            return int(value)
        if isinstance(default, float):
            return float(value)
    except (TypeError, ValueError):
        return default
    return value


def apply_overrides(overrides: dict[str, _Any]) -> dict[str, _Any]:
    """Apply an override map to this module in-place. Non-whitelisted keys are
    silently dropped. Returns the effective override map that was applied."""
    _snapshot_defaults()
    g = globals()
    applied: dict[str, _Any] = {}
    for k in OVERRIDABLE_KEYS:
        if k in overrides and overrides[k] is not None:
            v = _coerce(k, overrides[k])
            g[k] = v
            applied[k] = v
        else:
            # revert to default
            g[k] = _DEFAULTS[k]
    global _current_overrides
    _current_overrides = applied
    return applied


def _load_from_db() -> dict[str, _Any]:
    """Fetch the overrides row from Supabase. Returns {} on any failure."""
    try:
        from db.supabase import get_supabase_admin  # local import — avoid startup dep
    except Exception:
        return {}
    try:
        client = get_supabase_admin()
        r = (
            client.table("feed_config")
            .select("overrides")
            .eq("key", "default")
            .maybe_single()
            .execute()
        )
        data = getattr(r, "data", None) or {}
        raw = data.get("overrides") or {}
        return raw if isinstance(raw, dict) else {}
    except Exception as exc:
        _log.debug("feed_config load failed: %s", exc)
        return {}


def refresh_from_db(force: bool = False) -> dict[str, _Any]:
    """Load overrides from the DB (respecting a small in-process TTL cache)
    and apply them. Safe to call from a request path — a single query is
    made at most once per `_CACHE_TTL_SECONDS`."""
    global _last_load_ts
    now = _time.time()
    if not force and (now - _last_load_ts) < _CACHE_TTL_SECONDS:
        return dict(_current_overrides)
    _last_load_ts = now
    raw = _load_from_db()
    return apply_overrides(raw)


def invalidate_cache() -> None:
    """Force the next `refresh_from_db()` to hit the DB again."""
    global _last_load_ts
    _last_load_ts = 0.0
