from __future__ import annotations

import logging
from datetime import datetime, timezone

from db.supabase import get_supabase_admin

logger = logging.getLogger(__name__)


def list_active_boost_plans() -> list[dict]:
    """Return all active boost plans available for purchase."""
    admin = get_supabase_admin()
    try:
        r = (
            admin.table("boost_plans")
            .select("*")
            .eq("is_active", True)
            .order("price_amount")
            .execute()
        )
        return r.data or []
    except Exception as exc:
        logger.warning("list_active_boost_plans failed: %s", exc)
        return []


def purchase_boost(
    listing_id: str,
    seller_id: str,
    boost_plan_id: str,
    payment_reference: str | None = None,
) -> dict | None:
    """Purchase a boost for a listing. Returns the created boost record or None."""
    admin = get_supabase_admin()

    plan_r = admin.table("boost_plans").select("*").eq("id", boost_plan_id).execute()
    if not plan_r.data:
        raise ValueError("Boost plan not found")
    plan = plan_r.data[0]
    if not plan.get("is_active"):
        raise ValueError("Boost plan is not active")

    duration_hours = int(plan["duration_hours"])
    score_bonus = int(plan.get("score_bonus", 0))
    now = datetime.now(timezone.utc)
    starts_at = now
    ends_at = now.replace(hour=(now.hour + duration_hours) % 24)
    from datetime import timedelta
    ends_at = now + timedelta(hours=duration_hours)

    payload = {
        "listing_id": listing_id,
        "seller_id": seller_id,
        "boost_plan_id": boost_plan_id,
        "payment_status": "completed" if payment_reference else "pending",
        "payment_reference": payment_reference,
        "score_bonus": score_bonus,
        "starts_at": starts_at.isoformat(),
        "ends_at": ends_at.isoformat(),
        "active": True,
    }
    r = admin.table("listing_boosts").insert(payload).execute()
    if not r.data:
        return None
    result = r.data[0]

    from ranking.service import calculate_listing_score, calculate_shop_seller_score
    product_r = admin.table("products").select("shop_id").eq("id", listing_id).execute()
    if product_r.data:
        shop_id = product_r.data[0].get("shop_id")
        if shop_id:
            calculate_listing_score(listing_id)
            calculate_shop_seller_score(str(shop_id))

    return result


def expire_stale_boosts() -> int:
    """Deactivate boosts that have passed their end time. Returns count expired."""
    admin = get_supabase_admin()
    now = datetime.now(timezone.utc).isoformat()
    try:
        r = (
            admin.table("listing_boosts")
            .update({"active": False})
            .eq("active", True)
            .lt("ends_at", now)
            .execute()
        )
        expired = len(r.data or [])
        if expired:
            for boost in (r.data or []):
                listing_id = boost.get("listing_id")
                if listing_id:
                    from ranking.service import calculate_listing_score
                    calculate_listing_score(str(listing_id))
        return expired
    except Exception as exc:
        logger.warning("expire_stale_boosts failed: %s", exc)
        return 0


def get_active_boost_for_listing(listing_id: str) -> dict | None:
    """Return active boost for a listing if any."""
    admin = get_supabase_admin()
    try:
        r = (
            admin.table("listing_boosts")
            .select("*, boost_plans(name, duration_hours, price_amount)")
            .eq("listing_id", listing_id)
            .eq("active", True)
            .gte("ends_at", datetime.now(timezone.utc).isoformat())
            .limit(1)
            .execute()
        )
        return r.data[0] if r.data else None
    except Exception as exc:
        logger.warning("get_active_boost_for_listing(%s) failed: %s", listing_id, exc)
        return None
