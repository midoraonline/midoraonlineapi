"""Merchant listing actions: boost, repost, and live feed position."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException

logger = logging.getLogger(__name__)

FEED_SCAN_PAGE = 72
FEED_SCAN_MAX_PAGES = 15


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _err(status: int, msg: str, code: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"detail": msg, "code": code})


def _parse(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def owned_listing(admin: Any, listing_id: str, user_id: str) -> dict[str, Any]:
    rows = (
        admin.table("products")
        .select("id,shop_id,title,category,status,is_published,created_at,first_posted_at,repost_count")
        .eq("id", listing_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    if not rows:
        raise _err(404, "Listing not found.", "not_found")
    product = rows[0]
    shop = admin.table("shops").select("owner_id").eq("id", product.get("shop_id")).limit(1).execute().data or []
    if not shop or str(shop[0].get("owner_id")) != str(user_id):
        raise _err(403, "You don't own this listing.", "forbidden")
    return product


def _plan(admin: Any, user_id: str) -> dict[str, Any]:
    from payments.plan_service import get_user_plan

    try:
        return get_user_plan(admin, user_id)
    except Exception:  # noqa: BLE001
        from payments.plans import get_plan

        return get_plan(None)


def _month_start(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def boost_allowance(admin: Any, user_id: str, plan: dict[str, Any]) -> dict[str, int]:
    per_month = int(plan.get("free_boosts_per_month") or 0)
    used = 0
    try:
        r = (
            admin.table("listing_boosts")
            .select("id", count="exact")
            .eq("seller_id", user_id)
            .eq("source", "plan_allowance")
            .gte("created_at", _month_start(_now()).isoformat())
            .execute()
        )
        used = int(r.count or len(r.data or []))
    except Exception as exc:  # noqa: BLE001
        logger.warning("boost allowance count failed: %s", exc)
    return {"per_month": per_month, "used": used, "remaining": max(0, per_month - used)}


def active_boost(admin: Any, listing_id: str) -> dict[str, Any] | None:
    try:
        r = (
            admin.table("listing_boosts")
            .select("id,ends_at,score_bonus,source,payment_status")
            .eq("listing_id", listing_id)
            .eq("active", True)
            .gte("ends_at", _now().isoformat())
            .order("ends_at", desc=True)
            .limit(1)
            .execute()
        )
        return (r.data or [None])[0]
    except Exception:  # noqa: BLE001
        return None


def repost_status(admin: Any, listing: dict[str, Any], user_id: str, plan: dict[str, Any]) -> dict[str, Any]:
    per_day = int(plan.get("reposts_per_day") or 0)
    cooldown = int(plan.get("repost_cooldown_hours") or 0)
    now = _now()
    used_today = 0
    last_for_listing: datetime | None = None
    try:
        r = (
            admin.table("listing_reposts")
            .select("listing_id,created_at")
            .eq("user_id", user_id)
            .gte("created_at", (now - timedelta(hours=24)).isoformat())
            .execute()
        )
        rows = r.data or []
        used_today = len(rows)
        mine = [_parse(x.get("created_at")) for x in rows if str(x.get("listing_id")) == str(listing["id"])]
        mine = [m for m in mine if m]
        last_for_listing = max(mine) if mine else None
        if cooldown > 24 and last_for_listing is None:
            older = (
                admin.table("listing_reposts")
                .select("created_at")
                .eq("listing_id", listing["id"])
                .order("created_at", desc=True)
                .limit(1)
                .execute()
                .data
                or []
            )
            last_for_listing = _parse(older[0]["created_at"]) if older else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("repost status failed: %s", exc)
    next_at = last_for_listing + timedelta(hours=cooldown) if last_for_listing and cooldown else None
    reason = None
    if per_day <= 0:
        reason = "Your plan doesn't include reposts."
    elif used_today >= per_day:
        reason = f"You've used all {per_day} reposts for the last 24 hours."
    elif next_at and next_at > now:
        reason = "This ad was reposted recently."
    return {
        "per_day": per_day,
        "used_today": used_today,
        "cooldown_hours": cooldown,
        "next_allowed_at": next_at.isoformat() if next_at and next_at > now else None,
        "can_repost": reason is None and listing.get("status") == "active" and bool(listing.get("is_published")),
        "reason": reason
        or (None if listing.get("status") == "active" and listing.get("is_published") else "Only live ads can be reposted."),
    }


def promote_options(admin: Any, listing_id: str, user_id: str) -> dict[str, Any]:
    listing = owned_listing(admin, listing_id, user_id)
    plan = _plan(admin, user_id)
    try:
        packages = (
            admin.table("boost_plans").select("id,name,duration_hours,price_amount,score_bonus")
            .eq("is_active", True).order("price_amount").execute().data or []
        )
    except Exception:  # noqa: BLE001
        packages = []
    return {
        "listing_id": listing_id,
        "plan": {"key": plan.get("key"), "name": plan.get("name")},
        "packages": packages,
        "allowance": boost_allowance(admin, user_id, plan),
        "active_boost": active_boost(admin, listing_id),
        "repost": repost_status(admin, listing, user_id, plan),
    }


def boost_listing(admin: Any, listing_id: str, user_id: str, package_id: str, use_allowance: bool) -> dict[str, Any]:
    listing = owned_listing(admin, listing_id, user_id)
    if listing.get("status") != "active" or not listing.get("is_published"):
        raise _err(409, "Only live ads can be boosted.", "not_live")
    if active_boost(admin, listing_id):
        raise _err(409, "This ad is already boosted.", "already_boosted")
    pkg = admin.table("boost_plans").select("*").eq("id", package_id).limit(1).execute().data or []
    if not pkg or not pkg[0].get("is_active"):
        raise _err(404, "Boost package not found.", "not_found")
    pkg = pkg[0]
    plan = _plan(admin, user_id)
    now = _now()
    row = {
        "listing_id": listing_id,
        "seller_id": user_id,
        "boost_plan_id": pkg["id"],
        "score_bonus": int(pkg.get("score_bonus") or 0),
        "starts_at": now.isoformat(),
        "ends_at": (now + timedelta(hours=int(pkg["duration_hours"]))).isoformat(),
    }
    if use_allowance:
        if boost_allowance(admin, user_id, plan)["remaining"] <= 0:
            raise _err(402, "No free boosts left this month on your plan.", "no_allowance")
        row.update({"source": "plan_allowance", "payment_status": "completed", "active": True})
        created = (admin.table("listing_boosts").insert(row).execute().data or [row])[0]
        _after_boost_change(listing_id)
        return {"status": "active", "boost": created}
    # Paid boost: recorded as pending. Activation happens in activate_paid_boost()
    # once a payment provider confirms (Pesapal hook not wired for boosts yet).
    row.update({"source": "paid", "payment_status": "pending", "active": False})
    created = (admin.table("listing_boosts").insert(row).execute().data or [row])[0]
    return {
        "status": "payment_required",
        "boost": created,
        "message": "Paid boosts aren't available to buy online yet. Use a free plan boost or contact support.",
    }


def activate_paid_boost(admin: Any, boost_id: str, payment_reference: str) -> bool:
    """Payment hook: call when a boost payment is confirmed (e.g. from the Pesapal webhook)."""
    r = (
        admin.table("listing_boosts")
        .update({"payment_status": "completed", "payment_reference": payment_reference, "active": True})
        .eq("id", boost_id)
        .execute()
    )
    rows = r.data or []
    if rows:
        _after_boost_change(str(rows[0].get("listing_id")))
    return bool(rows)


def _after_boost_change(listing_id: str) -> None:
    try:
        from ranking.service import calculate_listing_score

        calculate_listing_score(listing_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("rescore after boost failed: %s", exc)
    _invalidate_guest_order()


def _invalidate_guest_order() -> None:
    try:
        from db.supabase import get_supabase_admin
        from feed import snapshots

        snapshots.invalidate(get_supabase_admin())
    except Exception as exc:  # noqa: BLE001
        logger.warning("guest order invalidate failed: %s", exc)
    try:
        from feed import composite

        composite._guest_home_cache.clear()
    except Exception:  # noqa: BLE001
        pass


def repost_listing(admin: Any, listing_id: str, user_id: str) -> dict[str, Any]:
    listing = owned_listing(admin, listing_id, user_id)
    plan = _plan(admin, user_id)
    status = repost_status(admin, listing, user_id, plan)
    if not status["can_repost"]:
        raise _err(429, status["reason"] or "You can't repost this ad right now.", "repost_limited")
    now = _now().isoformat()
    patch: dict[str, Any] = {"created_at": now, "repost_count": int(listing.get("repost_count") or 0) + 1}
    if not listing.get("first_posted_at"):
        patch["first_posted_at"] = listing.get("created_at")
    admin.table("products").update(patch).eq("id", listing_id).execute()
    admin.table("listing_reposts").insert({"listing_id": listing_id, "user_id": user_id}).execute()
    try:
        from ranking.service import calculate_listing_score

        calculate_listing_score(listing_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("rescore after repost failed: %s", exc)
    _invalidate_guest_order()
    return {"status": "reposted", "posted_at": now, "repost": repost_status(admin, {**listing, **patch}, user_id, plan)}


def _scan_position(listing_id: str, *, category: str | None) -> tuple[int | None, int]:
    """1-based position in the public feed (same ranking as /feed/home for guests)."""
    from db.supabase import get_supabase_admin
    from feed.catalog import ListingFilters
    from feed.service import get_algorithm_feed

    db = get_supabase_admin()
    filters = ListingFilters(category=category) if category else None
    seen = 0
    for page in range(1, FEED_SCAN_MAX_PAGES + 1):
        cards, has_more, _ = get_algorithm_feed(
            db, user_id=None, page=page, limit=FEED_SCAN_PAGE, category=category, filters=filters
        )
        for i, card in enumerate(cards):
            if str(card.id) == str(listing_id):
                return seen + i + 1, seen + len(cards)
        seen += len(cards)
        if not has_more or not cards:
            break
    return None, seen


def feed_position(admin: Any, listing_id: str, user_id: str) -> dict[str, Any]:
    listing = owned_listing(admin, listing_id, user_id)
    live = listing.get("status") == "active" and bool(listing.get("is_published"))
    if not live:
        return {"listing_id": listing_id, "live": False, "position": None, "category_position": None, "category": listing.get("category")}
    position, scanned = _scan_position(listing_id, category=None)
    cat = listing.get("category")
    cat_position, cat_scanned = _scan_position(listing_id, category=cat) if cat else (None, 0)
    return {
        "listing_id": listing_id,
        "live": True,
        "position": position,
        "scanned": scanned,
        "category": cat,
        "category_position": cat_position,
        "category_scanned": cat_scanned,
        "boosted": active_boost(admin, listing_id) is not None,
        "computed_at": _now().isoformat(),
    }
