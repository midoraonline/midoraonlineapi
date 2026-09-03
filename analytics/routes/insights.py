"""Admin insight queries over `analytics_events` (+ existing tables).

Every metric in the insights doc is computed here on demand — none of these
are stored as columns. Recompute > migrate when definitions change.

Each endpoint returns a small, plot-ready payload so the admin dashboard
can dumb-pipe it into Recharts without further shaping.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from db.supabase import get_supabase_admin

logger = logging.getLogger(__name__)

router = APIRouter()

# WhatsApp click is the primary conversion proxy per the insights doc.
CONVERSION_EVENTS = ("conversion:whatsapp_click", "listing:whatsapp_click")


def _window_bounds(days: int) -> tuple[str, str]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=max(1, days))
    return start.isoformat(), end.isoformat()


def _rows(admin: Any, event_type: str, since_iso: str) -> list[dict[str, Any]]:
    """Cheap generic fetch for an event type in a window (paginated once)."""
    return (
        admin.table("analytics_events")
        .select("actor_id, session_id, target_type, target_id, properties, ts")
        .eq("event_type", event_type)
        .gte("ts", since_iso)
        .limit(10000)
        .execute()
        .data
        or []
    )


# ────────────────────────────────────────────────────────────────────────
# Marketplace discovery & liquidity
# ────────────────────────────────────────────────────────────────────────


class CategoryFillRateRow(BaseModel):
    category: str
    browses: int
    filled_browses: int  # browses that returned >= min_results verified listings
    fill_rate: float


class CategoryFillRateResponse(BaseModel):
    window_days: int
    min_results: int
    rows: list[CategoryFillRateRow]


@router.get("/category-fill-rate", response_model=CategoryFillRateResponse)
async def category_fill_rate(
    window_days: int = Query(30, ge=1, le=365),
    min_results: int = Query(3, ge=1, le=50),
) -> CategoryFillRateResponse:
    """% of category/search browses that surface `min_results`+ verified listings."""
    since, _ = _window_bounds(window_days)
    admin = get_supabase_admin()

    events = _rows(admin, "marketplace:search", since)

    buckets: dict[str, dict[str, int]] = {}
    for ev in events:
        props = ev.get("properties") or {}
        category = str(props.get("category") or "(none)").strip() or "(none)"
        try:
            verified = int(props.get("verifiedCount", 0) or 0)
        except (TypeError, ValueError):
            verified = 0
        b = buckets.setdefault(category, {"browses": 0, "filled": 0})
        b["browses"] += 1
        if verified >= min_results:
            b["filled"] += 1

    rows = [
        CategoryFillRateRow(
            category=cat,
            browses=v["browses"],
            filled_browses=v["filled"],
            fill_rate=(v["filled"] / v["browses"]) if v["browses"] else 0.0,
        )
        for cat, v in sorted(buckets.items(), key=lambda kv: kv[1]["browses"], reverse=True)
    ]
    return CategoryFillRateResponse(
        window_days=window_days, min_results=min_results, rows=rows
    )


# ────────────────────────────────────────────────────────────────────────
# Search → contact rate (marketplace-level conversion proxy)
# ────────────────────────────────────────────────────────────────────────


class SearchToContactResponse(BaseModel):
    window_days: int
    sessions_with_search: int
    sessions_with_contact: int
    conversion_rate: float


@router.get("/search-to-contact", response_model=SearchToContactResponse)
async def search_to_contact(
    window_days: int = Query(30, ge=1, le=365),
) -> SearchToContactResponse:
    """Sessions with any search → sessions with any WhatsApp click within the window."""
    since, _ = _window_bounds(window_days)
    admin = get_supabase_admin()

    search_sessions = {
        r.get("session_id")
        for r in _rows(admin, "marketplace:search", since)
        if r.get("session_id")
    }
    contact_sessions: set[str] = set()
    for et in CONVERSION_EVENTS:
        for r in _rows(admin, et, since):
            sid = r.get("session_id")
            if sid:
                contact_sessions.add(sid)

    both = search_sessions & contact_sessions
    return SearchToContactResponse(
        window_days=window_days,
        sessions_with_search=len(search_sessions),
        sessions_with_contact=len(both),
        conversion_rate=(len(both) / len(search_sessions)) if search_sessions else 0.0,
    )


# ────────────────────────────────────────────────────────────────────────
# Verification funnel
# ────────────────────────────────────────────────────────────────────────


class VerificationFunnelStep(BaseModel):
    step: str
    count: int


class VerificationFunnelResponse(BaseModel):
    window_days: int
    steps: list[VerificationFunnelStep]


@router.get("/verification-funnel", response_model=VerificationFunnelResponse)
async def verification_funnel(
    window_days: int = Query(90, ge=1, le=365),
) -> VerificationFunnelResponse:
    """Started → submitted → approved/rejected counts from analytics_events."""
    since, _ = _window_bounds(window_days)
    admin = get_supabase_admin()

    started = len(_rows(admin, "merchant:verification_started", since))
    submitted = len(_rows(admin, "merchant:verification_submitted", since))
    completions = _rows(admin, "merchant:verification_completed", since)
    approved = sum(
        1 for r in completions if ((r.get("properties") or {}).get("status") == "approved")
    )
    rejected = sum(
        1 for r in completions if ((r.get("properties") or {}).get("status") == "rejected")
    )

    return VerificationFunnelResponse(
        window_days=window_days,
        steps=[
            VerificationFunnelStep(step="started", count=started),
            VerificationFunnelStep(step="submitted", count=submitted),
            VerificationFunnelStep(step="approved", count=approved),
            VerificationFunnelStep(step="rejected", count=rejected),
        ],
    )


# ────────────────────────────────────────────────────────────────────────
# Stale-listing rate — no view in N days
# ────────────────────────────────────────────────────────────────────────


class StaleListingResponse(BaseModel):
    window_days: int
    active_listings: int
    stale_listings: int
    stale_rate: float


@router.get("/stale-listing-rate", response_model=StaleListingResponse)
async def stale_listing_rate(
    window_days: int = Query(14, ge=1, le=180),
) -> StaleListingResponse:
    """Active published listings with zero listing_events / analytics_events views in the window."""
    admin = get_supabase_admin()
    horizon = datetime.now(timezone.utc) - timedelta(days=window_days)
    horizon_iso = horizon.isoformat()

    products = (
        admin.table("products")
        .select("id, is_published")
        .eq("is_published", True)
        .limit(20000)
        .execute()
        .data
        or []
    )
    active = len(products)
    if not active:
        return StaleListingResponse(
            window_days=window_days, active_listings=0, stale_listings=0, stale_rate=0.0
        )
    active_ids = {p["id"] for p in products}

    fresh: set[str] = set()
    try:
        r = (
            admin.table("listing_events")
            .select("listing_id")
            .eq("event_type", "viewed")
            .gte("created_at", horizon_iso)
            .limit(200000)
            .execute()
        )
        for row in r.data or []:
            lid = row.get("listing_id")
            if lid:
                fresh.add(lid)
    except Exception as exc:
        logger.warning("stale-listing-rate: listing_events lookup failed: %s", exc)

    # Also count analytics_events listing views (new event surface, once wired).
    try:
        r2 = (
            admin.table("analytics_events")
            .select("target_id")
            .eq("event_type", "listing:viewed")
            .gte("ts", horizon_iso)
            .limit(200000)
            .execute()
        )
        for row in r2.data or []:
            tid = row.get("target_id")
            if tid:
                fresh.add(tid)
    except Exception as exc:
        logger.warning("stale-listing-rate: analytics_events lookup failed: %s", exc)

    stale = sum(1 for pid in active_ids if pid not in fresh)
    return StaleListingResponse(
        window_days=window_days,
        active_listings=active,
        stale_listings=stale,
        stale_rate=stale / active,
    )


# ────────────────────────────────────────────────────────────────────────
# View → WhatsApp click rate, per shop
# ────────────────────────────────────────────────────────────────────────


class ShopConversionRow(BaseModel):
    shop_id: str
    shop_name: str | None
    views: int
    whatsapp_clicks: int
    view_to_click_rate: float


class ShopConversionResponse(BaseModel):
    window_days: int
    rows: list[ShopConversionRow]


@router.get("/shop-conversion", response_model=ShopConversionResponse)
async def shop_conversion(
    window_days: int = Query(30, ge=1, le=365),
    limit: int = Query(25, ge=1, le=200),
) -> ShopConversionResponse:
    """view_to_click_rate per shop over the window. Falls back to listing_events when
    analytics_events has no data yet (bootstrap period after migration)."""
    admin = get_supabase_admin()
    since, _ = _window_bounds(window_days)

    counters: dict[str, dict[str, int]] = {}

    def _bump(shop_id: str, key: str) -> None:
        if not shop_id:
            return
        c = counters.setdefault(shop_id, {"views": 0, "clicks": 0})
        c[key] += 1

    for ev in _rows(admin, "listing:viewed", since) + _rows(admin, "shop:viewed", since):
        props = ev.get("properties") or {}
        _bump(str(props.get("shopId") or ev.get("target_id") or ""), "views")

    for et in CONVERSION_EVENTS:
        for ev in _rows(admin, et, since):
            props = ev.get("properties") or {}
            _bump(str(props.get("shopId") or ""), "clicks")

    # Bootstrap fallback from listing_events (existing surface).
    try:
        le = (
            admin.table("listing_events")
            .select("listing_id, event_type")
            .in_("event_type", ["viewed", "whatsapp_clicked"])
            .gte("created_at", since)
            .limit(200000)
            .execute()
            .data
            or []
        )
        product_ids = {r["listing_id"] for r in le if r.get("listing_id")}
        if product_ids:
            shop_map = (
                admin.table("products")
                .select("id, shop_id")
                .in_("id", list(product_ids))
                .limit(20000)
                .execute()
                .data
                or []
            )
            id_to_shop = {r["id"]: r.get("shop_id") for r in shop_map}
            for r in le:
                shop_id = id_to_shop.get(r["listing_id"])
                if not shop_id:
                    continue
                if r["event_type"] == "viewed":
                    _bump(str(shop_id), "views")
                else:
                    _bump(str(shop_id), "clicks")
    except Exception as exc:
        logger.warning("shop-conversion: listing_events fallback failed: %s", exc)

    if not counters:
        return ShopConversionResponse(window_days=window_days, rows=[])

    shop_ids = list(counters.keys())
    names = (
        admin.table("shops")
        .select("id, name")
        .in_("id", shop_ids)
        .limit(len(shop_ids))
        .execute()
        .data
        or []
    )
    id_to_name = {r["id"]: r.get("name") for r in names}

    rows = [
        ShopConversionRow(
            shop_id=sid,
            shop_name=id_to_name.get(sid),
            views=c["views"],
            whatsapp_clicks=c["clicks"],
            view_to_click_rate=(c["clicks"] / c["views"]) if c["views"] else 0.0,
        )
        for sid, c in counters.items()
    ]
    # Rank by clicks descending — the shops actually converting are what admin cares about.
    rows.sort(key=lambda r: (r.whatsapp_clicks, r.view_to_click_rate), reverse=True)
    return ShopConversionResponse(window_days=window_days, rows=rows[:limit])


# ────────────────────────────────────────────────────────────────────────
# Rating / review coverage
# ────────────────────────────────────────────────────────────────────────


class RatingCoverageResponse(BaseModel):
    window_days: int
    prompted: int
    submitted: int
    coverage_rate: float


@router.get("/rating-coverage", response_model=RatingCoverageResponse)
async def rating_coverage(
    window_days: int = Query(30, ge=1, le=365),
) -> RatingCoverageResponse:
    since, _ = _window_bounds(window_days)
    admin = get_supabase_admin()
    prompted = len(_rows(admin, "trust:rating_prompted", since))
    submitted = len(_rows(admin, "trust:rating_submitted", since))
    return RatingCoverageResponse(
        window_days=window_days,
        prompted=prompted,
        submitted=submitted,
        coverage_rate=(submitted / prompted) if prompted else 0.0,
    )


# ────────────────────────────────────────────────────────────────────────
# Report / scam-flag rate per 1k active listings
# ────────────────────────────────────────────────────────────────────────


class ReportRateBucket(BaseModel):
    bucket: str
    reports: int
    active_listings: int
    reports_per_thousand: float


class ReportRateResponse(BaseModel):
    window_days: int
    rows: list[ReportRateBucket]


@router.get("/report-rate", response_model=ReportRateResponse)
async def report_rate(
    window_days: int = Query(30, ge=1, le=365),
    dimension: str = Query("category", pattern="^(category|shop)$"),
) -> ReportRateResponse:
    since, _ = _window_bounds(window_days)
    admin = get_supabase_admin()
    reports = _rows(admin, "trust:report_submitted", since)

    by_bucket: dict[str, int] = {}
    for r in reports:
        props = r.get("properties") or {}
        key = str(props.get(dimension) or "(none)")
        by_bucket[key] = by_bucket.get(key, 0) + 1

    # Denominator: active published products, bucketed by the same dimension.
    try:
        products = (
            admin.table("products")
            .select("id, category, shop_id")
            .eq("is_published", True)
            .limit(50000)
            .execute()
            .data
            or []
        )
    except Exception as exc:
        logger.warning("report-rate: products lookup failed: %s", exc)
        products = []

    active_by_bucket: dict[str, int] = {}
    for p in products:
        if dimension == "category":
            key = str(p.get("category") or "(none)")
        else:
            key = str(p.get("shop_id") or "(none)")
        active_by_bucket[key] = active_by_bucket.get(key, 0) + 1

    rows = []
    for bucket, count in sorted(by_bucket.items(), key=lambda kv: kv[1], reverse=True):
        active = active_by_bucket.get(bucket, 0)
        per_k = (count * 1000 / active) if active else 0.0
        rows.append(
            ReportRateBucket(
                bucket=bucket,
                reports=count,
                active_listings=active,
                reports_per_thousand=per_k,
            )
        )

    return ReportRateResponse(window_days=window_days, rows=rows)


# ────────────────────────────────────────────────────────────────────────
# Discount engagement — do discount badges move the needle?
# ────────────────────────────────────────────────────────────────────────


class DiscountEngagementResponse(BaseModel):
    window_days: int
    with_discount: dict[str, int | float]
    without_discount: dict[str, int | float]


@router.get("/discount-engagement", response_model=DiscountEngagementResponse)
async def discount_engagement(
    window_days: int = Query(30, ge=1, le=365),
) -> DiscountEngagementResponse:
    since, _ = _window_bounds(window_days)
    admin = get_supabase_admin()

    views = _rows(admin, "listing:viewed", since)
    clicks: list[dict[str, Any]] = []
    for et in CONVERSION_EVENTS:
        clicks.extend(_rows(admin, et, since))

    def split(rows: list[dict[str, Any]]) -> tuple[int, int]:
        with_d = 0
        without_d = 0
        for r in rows:
            has = bool((r.get("properties") or {}).get("hasDiscount"))
            if has:
                with_d += 1
            else:
                without_d += 1
        return with_d, without_d

    vw, vwo = split(views)
    cw, cwo = split(clicks)
    return DiscountEngagementResponse(
        window_days=window_days,
        with_discount={
            "views": vw,
            "clicks": cw,
            "view_to_click_rate": (cw / vw) if vw else 0.0,
        },
        without_discount={
            "views": vwo,
            "clicks": cwo,
            "view_to_click_rate": (cwo / vwo) if vwo else 0.0,
        },
    )


# ────────────────────────────────────────────────────────────────────────
# Roll-up: daily event trend for admin overview
# ────────────────────────────────────────────────────────────────────────


class EventTrendPoint(BaseModel):
    day: str
    event_count: int
    unique_actors: int


class EventTrendResponse(BaseModel):
    event_type: str
    window_days: int
    points: list[EventTrendPoint]


@router.get("/event-trend", response_model=EventTrendResponse)
async def event_trend(
    event_type: str = Query(..., min_length=1, max_length=80),
    window_days: int = Query(30, ge=1, le=365),
) -> EventTrendResponse:
    since, _ = _window_bounds(window_days)
    admin = get_supabase_admin()
    try:
        r = (
            admin.table("analytics_events_daily")
            .select("day, event_count, unique_actors")
            .eq("event_type", event_type)
            .gte("day", since)
            .order("day", desc=False)
            .limit(400)
            .execute()
        )
    except Exception as exc:
        logger.warning("event_trend(%s) failed: %s", event_type, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="analytics view unavailable",
        ) from exc
    points = [
        EventTrendPoint(
            day=str(row["day"])[:10],
            event_count=int(row.get("event_count") or 0),
            unique_actors=int(row.get("unique_actors") or 0),
        )
        for row in r.data or []
    ]
    return EventTrendResponse(
        event_type=event_type, window_days=window_days, points=points
    )
