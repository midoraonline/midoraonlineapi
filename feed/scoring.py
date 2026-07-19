"""Pure product scoring for the Midora feed.

Given per-user signals + per-product side-signal maps, return a numeric
score for a single product. No I/O, no DB — easy to unit-test.

The scoring formula follows the Midora Feed Composition spec:

    FeedScore =
        + Taste Match (cosine × 80)
        + Category Match Bonus     (fallback when no vectors)  up to +30
        + Search Match Bonus       (+20 per match, max +40)
        + Followed Shop Bonus      (+50)
        + Freshness Bonus          (0-24h +40 down to 14d+ 0)
        + Velocity Bonus           (saturating, cap +40)
        + New Seller Bonus         (+30 if age<30d OR views<500)
        + Seller Quality Bonus     (up to +25)
        + Global Popularity Score  (capped at +30)
        + Premium Store Bonus      (+15 if active subscription)
        + Sponsored Listing Bonus  (+25 — applied by placement, not scoring)
        + Boosted Listing Bonus    (+50 via listing_boosts.score_bonus)
        + Super Boost Bonus        (+75 — placement-driven)
        - Seen Demotion            (-50 if user already interacted)
        - Vendor Diversity Penalty (applied by placement engine)
        - Fraud Risk Penalty       (0 / -50 / -200 / hidden)

Exploration slots are chosen by the placement engine and receive
`EXPLORATION_BONUS` when scored inside their reserved slot pool.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from feed import config as C
from feed.embeddings import cosine_similarity, parse_embedding
from feed.signals import parse_timestamp


# ---------------------------------------------------------------------------
# Individual bonus/penalty helpers — one function per line item in the spec
# ---------------------------------------------------------------------------

def taste_match(user_vector: list[float] | None, product_embedding: Any) -> float:
    if not user_vector:
        return 0.0
    vec = parse_embedding(product_embedding)
    if not vec:
        return 0.0
    sim = cosine_similarity(user_vector, vec)
    return max(sim, 0.0) * C.VECTOR_SCORE_SCALE


def category_match_fallback(
    product: dict[str, Any],
    signals: dict[str, Any],
    used_vector: bool,
) -> float:
    """Only fires when there was no vector match — pure fallback signal."""
    if used_vector:
        return 0.0
    cat = (product.get("category") or "").strip()
    if cat and cat in signals["categories"]:
        return C.CATEGORY_MATCH_BOOST
    return 0.0


def search_match(product: dict[str, Any], signals: dict[str, Any]) -> float:
    title = (product.get("title") or "").lower()
    desc = (product.get("description") or "").lower()
    hits = 0
    for term in signals["search_terms"]:
        if term and (term in title or term in desc):
            hits += 1
            if hits >= C.SEARCH_MATCH_MAX_HITS:
                break
    return hits * C.SEARCH_MATCH_PER_MATCH


def followed_shop(product: dict[str, Any], signals: dict[str, Any]) -> float:
    shop_id = str(product.get("shop_id", ""))
    if shop_id and shop_id in signals["followed_shop_ids"]:
        return C.FOLLOWED_SHOP_BOOST
    return 0.0


def freshness_bonus(product: dict[str, Any], now: datetime) -> float:
    ts = parse_timestamp(product.get("created_at"))
    if ts is None:
        return 0.0
    hours = max((now - ts).total_seconds() / 3600.0, 0.0)
    for cutoff, bonus in C.FRESHNESS_CURVE:
        if hours <= cutoff:
            return bonus
    return C.FRESHNESS_FALLBACK


def velocity_bonus(product: dict[str, Any], velocity_map: dict[str, float]) -> float:
    pid = str(product.get("id", ""))
    raw = velocity_map.get(pid, 0.0)
    if raw <= 0:
        return 0.0
    return C.VELOCITY_MAX_BONUS * (1.0 - math.exp(-raw / C.VELOCITY_HALF_LIFE))


def new_seller_bonus(
    product: dict[str, Any],
    shop_meta: dict[str, dict[str, Any]],
    now: datetime,
) -> float:
    shop_id = str(product.get("shop_id", ""))
    meta = shop_meta.get(shop_id)
    if not meta:
        return 0.0
    created = parse_timestamp(meta.get("created_at"))
    is_young = False
    if created:
        age_days = (now - created).total_seconds() / 86400.0
        if age_days < C.NEW_SELLER_MAX_AGE_DAYS:
            is_young = True
    impressions = int(product.get("view_count") or 0)
    low_exposure = impressions < C.NEW_SELLER_MAX_IMPRESSIONS
    if is_young or low_exposure:
        return C.NEW_SELLER_BONUS
    return 0.0


def seller_quality_bonus(
    product: dict[str, Any],
    shop_meta: dict[str, dict[str, Any]],
) -> float:
    """Derived from trust_score, seller_score, verification flag and availability."""
    shop_id = str(product.get("shop_id", ""))
    meta = shop_meta.get(shop_id)
    if not meta:
        return 0.0
    # trust_score: 0..5, seller_score: unbounded numeric
    trust = float(meta.get("trust_score") or 0.0)
    seller = float(meta.get("seller_score") or 0.0)
    badges = meta.get("trust_badges") or []
    verified = "verified" in badges if isinstance(badges, list) else False
    available = bool(meta.get("available_now", False))
    active = bool(meta.get("is_active", False))

    # Weighted composition (bounded by SELLER_QUALITY_CAP)
    score = 0.0
    score += min(trust / 5.0, 1.0) * 12.0        # up to +12
    score += min(seller / 100.0, 1.0) * 8.0      # up to +8
    score += 3.0 if verified else 0.0
    score += 1.5 if available else 0.0
    score += 0.5 if active else 0.0
    return min(score, C.SELLER_QUALITY_CAP)


def global_popularity(product: dict[str, Any]) -> float:
    """`listing_score` is the DB-maintained aggregate popularity metric.

    Capped to prevent old viral listings from dominating newer inventory.
    """
    return min(float(product.get("listing_score") or 0.0), C.GLOBAL_POPULARITY_CAP)


def premium_store_bonus(
    product: dict[str, Any],
    shop_meta: dict[str, dict[str, Any]],
    now: datetime,
) -> float:
    shop_id = str(product.get("shop_id", ""))
    meta = shop_meta.get(shop_id)
    if not meta:
        return 0.0
    end = parse_timestamp(meta.get("subscription_end_date"))
    if end and end > now:
        return C.PREMIUM_STORE_BONUS
    return 0.0


def boost_bonus(product: dict[str, Any], boost_map: dict[str, dict[str, Any]]) -> float:
    """Reflects paid boost. `score_bonus` from `listing_boosts` is source-of-truth.

    We interpret score_bonus tiers as:
        <= 25   -> regular boost                (adds LISTING_BOOST_BONUS)
        > 25    -> super boost                  (adds SUPER_BOOST_BONUS)

    Sponsored slots are reserved by placement — no scoring impact here.
    """
    pid = str(product.get("id", ""))
    row = boost_map.get(pid)
    if not row:
        return 0.0
    sb = float(row.get("score_bonus") or 0.0)
    if sb > 25:
        return C.SUPER_BOOST_BONUS
    return C.LISTING_BOOST_BONUS


def seen_penalty(product: dict[str, Any], signals: dict[str, Any]) -> float:
    pid = str(product.get("id", ""))
    if (
        pid in signals["liked_product_ids"]
        or pid in signals["viewed_product_ids"]
        or pid in signals["saved_product_ids"]
    ):
        return -C.SEEN_DEMOTION
    return 0.0


def fraud_penalty(product: dict[str, Any], fraud_map: dict[str, str]) -> float | None:
    """Return None to hide the product entirely (critical fraud)."""
    pid = str(product.get("id", ""))
    severity = fraud_map.get(pid, "low")
    penalty = C.FRAUD_SEVERITY_PENALTY.get(severity, 0.0)
    return penalty  # None means "hide"


def build_exposure_multiplier(
    shop_impression_counts: dict[str, int],
) -> dict[str, float]:
    """Convert per-shop 24h impression counts into a score multiplier.

    multiplier = clamp(1 / (1 + (n / TARGET))^EXPONENT, MIN, 1.0)
    """
    out: dict[str, float] = {}
    target = C.EXPOSURE_TARGET_IMPRESSIONS
    exp = C.EXPOSURE_EXPONENT
    floor = C.EXPOSURE_MIN_MULTIPLIER
    for shop_id, n in shop_impression_counts.items():
        if not shop_id:
            continue
        denom = (1.0 + (max(n, 0) / target)) ** exp
        mult = 1.0 / denom if denom > 0 else 1.0
        out[shop_id] = max(floor, min(mult, 1.0))
    return out


# ---------------------------------------------------------------------------
# Composite score
# ---------------------------------------------------------------------------

def score_product(
    product: dict[str, Any],
    *,
    signals: dict[str, Any],
    user_vector: list[float] | None,
    shop_meta: dict[str, dict[str, Any]],
    velocity_map: dict[str, float],
    boost_map: dict[str, dict[str, Any]],
    fraud_map: dict[str, str],
    exposure_multiplier: dict[str, float] | None = None,
    now: datetime | None = None,
) -> float | None:
    """Return the composite score, or None if the product should be hidden."""
    if now is None:
        now = datetime.now(timezone.utc)

    fraud = fraud_penalty(product, fraud_map)
    if fraud is None:
        return None  # critical fraud -> hidden

    taste = taste_match(user_vector, product.get("embedding"))
    used_vector = taste > 0

    score = 0.0
    score += taste
    score += category_match_fallback(product, signals, used_vector)
    score += search_match(product, signals)
    score += followed_shop(product, signals)
    score += freshness_bonus(product, now)
    score += velocity_bonus(product, velocity_map)
    score += new_seller_bonus(product, shop_meta, now)
    score += seller_quality_bonus(product, shop_meta)
    score += global_popularity(product)
    score += premium_store_bonus(product, shop_meta, now)
    score += boost_bonus(product, boost_map)
    score += seen_penalty(product, signals)
    score += fraud

    # Exposure multiplier — applied AFTER additive components. Rotates
    # over-exposed sellers off the top without disrupting relative ranking
    # inside a single seller's own listings.
    if exposure_multiplier:
        shop_id = str(product.get("shop_id", ""))
        mult = exposure_multiplier.get(shop_id, 1.0)
        if mult != 1.0:
            score *= mult
    return score
