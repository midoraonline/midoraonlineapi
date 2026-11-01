from __future__ import annotations

from datetime import datetime
from typing import Any

from feed import config as C
from feed.signals import parse_timestamp

VERIFIED_BADGES = frozenset({"identity_verified", "business_verified", "professional_verified", "verified"})
HIDDEN_FRAUD = frozenset({"high", "critical"})


def freshness(product: dict[str, Any], now: datetime) -> float:
    ts = parse_timestamp(product.get("created_at"))
    if ts is None:
        return 0.0
    hours = max((now - ts).total_seconds() / 3600.0, 0.0)
    return 100.0 * 0.5 ** (hours / max(C.FRESHNESS_HALF_LIFE_HOURS, 1.0))


def trust(meta: dict[str, Any] | None) -> float:
    if not meta:
        return 0.0
    badges = meta.get("trust_badges") if isinstance(meta.get("trust_badges"), list) else []
    score = min(float(meta.get("trust_score") or 0) / 5.0, 1.0) * 50.0
    score += min(float(meta.get("seller_score") or 0) / 100.0, 1.0) * 20.0
    score += 30.0 if VERIFIED_BADGES.intersection(badges) else 0.0
    return score


def exposure_balance(multiplier: float) -> float:
    floor = C.EXPOSURE_MIN_MULTIPLIER
    if multiplier >= 1.0 or floor >= 1.0:
        return 100.0
    return max(0.0, (multiplier - floor) / (1.0 - floor)) * 100.0


def exposure_multipliers(shop_impressions: dict[str, int]) -> dict[str, float]:
    target = max(C.EXPOSURE_TARGET_IMPRESSIONS, 1.0)
    return {
        shop: max(C.EXPOSURE_MIN_MULTIPLIER, 1.0 / (1.0 + max(n, 0) / target))
        for shop, n in shop_impressions.items()
        if shop
    }


def engagement(product: dict[str, Any]) -> float:
    return min(float(product.get("listing_score") or 0), 100.0)


def components(
    product: dict[str, Any],
    *,
    quality: float,
    shop_meta: dict[str, Any] | None,
    multiplier: float,
    stage: int,
    now: datetime,
) -> dict[str, float]:
    return {
        "quality": quality * C.QUALITY_WEIGHT / 100.0,
        "freshness": freshness(product, now) * C.FRESHNESS_WEIGHT / 100.0,
        "trust": trust(shop_meta) * C.TRUST_WEIGHT / 100.0,
        "engagement": (
            engagement(product) * C.ENGAGEMENT_WEIGHT / 100.0 if stage >= C.ENGAGEMENT_MIN_STAGE else 0.0
        ),
        "exposure": exposure_balance(multiplier) * C.EXPOSURE_WEIGHT / 100.0,
    }


def score(
    product: dict[str, Any],
    *,
    quality: float,
    shop_meta: dict[str, Any] | None,
    fraud: str,
    multiplier: float,
    stage: int,
    now: datetime,
) -> float:
    total = sum(
        components(product, quality=quality, shop_meta=shop_meta, multiplier=multiplier, stage=stage, now=now).values()
    )
    return total - (C.FRAUD_MEDIUM_PENALTY if fraud == "medium" else 0.0)
