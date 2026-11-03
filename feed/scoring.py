from __future__ import annotations

from datetime import datetime
from typing import Any

from feed import config as C
from feed.expiry import deadline_end
from feed.signals import parse_timestamp
from feed.stages import lane_of

VERIFIED_BADGES = frozenset({"identity_verified", "business_verified", "professional_verified", "verified"})
HIDDEN_FRAUD = frozenset({"high", "critical"})

FRESHNESS_BANDS: tuple[tuple[float, float], ...] = (
    (6, 100.0),
    (24, 90.0),
    (72, 75.0),
    (120, 60.0),
    (192, 45.0),
    (360, 30.0),
    (744, 15.0),
)
FRESHNESS_FLOOR = 5.0
CLOSING_OPPORTUNITY_FRESHNESS = 60.0

EXPOSURE_BANDS: tuple[tuple[int, float], ...] = (
    (0, 100.0),
    (2, 80.0),
    (5, 60.0),
    (10, 40.0),
    (20, 20.0),
)
EXPOSURE_FLOOR = 5.0


def _band(hours: float) -> float:
    for limit, value in FRESHNESS_BANDS:
        if hours < limit:
            return value
    return FRESHNESS_FLOOR


def freshness(product: dict[str, Any], now: datetime) -> float:
    ts = parse_timestamp(product.get("created_at"))
    if ts is None:
        return 0.0
    hours = max((now - ts).total_seconds() / 3600.0, 0.0)
    lane = lane_of(product.get("item_type"))
    if lane == "service":
        hours /= max(C.SERVICE_FRESHNESS_STRETCH, 1.0)
    elif lane == "opportunity":
        hours /= max(C.OPPORTUNITY_FRESHNESS_STRETCH, 1.0)
    value = _band(hours)
    if lane == "opportunity":
        end = deadline_end(product.get("listing_meta"))
        if end is not None and 0 < (end - now).total_seconds() <= C.CLOSING_SOON_HOURS * 3600:
            value = max(value, CLOSING_OPPORTUNITY_FRESHNESS)
    return value


def exposure(impressions: int) -> float:
    for limit, value in EXPOSURE_BANDS:
        if impressions <= limit:
            return value
    return EXPOSURE_FLOOR


def weights(eligible: int) -> tuple[float, float, float]:
    if eligible >= C.MVP_LATE_MIN:
        return C.LATE_QUALITY_WEIGHT, C.LATE_FRESHNESS_WEIGHT, C.LATE_EXPOSURE_WEIGHT
    if eligible >= C.MVP_MID_MIN:
        return C.MID_QUALITY_WEIGHT, C.MID_FRESHNESS_WEIGHT, C.MID_EXPOSURE_WEIGHT
    return C.EARLY_QUALITY_WEIGHT, C.EARLY_FRESHNESS_WEIGHT, C.EARLY_EXPOSURE_WEIGHT


def trust(meta: dict[str, Any] | None) -> float:
    if not meta:
        return 0.0
    badges = meta.get("trust_badges") if isinstance(meta.get("trust_badges"), list) else []
    score = min(float(meta.get("trust_score") or 0) / 5.0, 1.0) * 50.0
    score += min(float(meta.get("seller_score") or 0) / 100.0, 1.0) * 20.0
    score += 30.0 if VERIFIED_BADGES.intersection(badges) else 0.0
    return score


def engagement(product: dict[str, Any]) -> float:
    return min(float(product.get("listing_score") or 0), 100.0)


def components(
    product: dict[str, Any],
    *,
    quality: float,
    shop_meta: dict[str, Any] | None,
    impressions: int,
    eligible: int,
    stage: int,
    now: datetime,
) -> dict[str, float]:
    wq, wf, we = weights(eligible)
    return {
        "quality": quality * wq / 100.0,
        "freshness": freshness(product, now) * wf / 100.0,
        "exposure": exposure(impressions) * we / 100.0,
        "trust": trust(shop_meta) * C.TRUST_WEIGHT / 100.0,
        "engagement": (
            engagement(product) * C.ENGAGEMENT_WEIGHT / 100.0 if stage >= C.ENGAGEMENT_MIN_STAGE else 0.0
        ),
    }


def score(
    product: dict[str, Any],
    *,
    quality: float,
    shop_meta: dict[str, Any] | None,
    fraud: str,
    impressions: int,
    eligible: int,
    stage: int,
    now: datetime,
) -> float:
    parts = components(
        product, quality=quality, shop_meta=shop_meta, impressions=impressions, eligible=eligible, stage=stage, now=now
    )
    return sum(parts.values()) - (C.FRAUD_MEDIUM_PENALTY if fraud == "medium" else 0.0)
