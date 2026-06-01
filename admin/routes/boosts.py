from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Query

from db.supabase import get_supabase_admin

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/boosts/overview")
async def admin_boost_overview(
    days: int = Query(30, ge=1, le=365),
) -> dict[str, Any]:
    """Admin: boost analytics — revenue, active boosts, top sellers."""
    admin = get_supabase_admin()
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=days)

    boosts = []
    try:
        r = (
            admin.table("listing_boosts")
            .select("*, boost_plans(name, price_amount)")
            .gte("created_at", window_start.isoformat())
            .execute()
        )
        boosts = r.data or []
    except Exception as exc:
        logger.warning("boost fetch failed: %s", exc)

    total_revenue = 0.0
    completed_count = 0
    active_count = 0
    plan_breakdown: dict[str, dict] = {}
    seller_revenue: dict[str, float] = {}

    for b in boosts:
        plan_name = (b.get("boost_plans") or {}).get("name", "Unknown")
        price = float((b.get("boost_plans") or {}).get("price_amount", 0))
        pstatus = b.get("payment_status", "")

        if pstatus == "completed":
            total_revenue += price
            completed_count += 1

        if pstatus == "completed":
            plan_breakdown.setdefault(plan_name, {"count": 0, "revenue": 0.0})
            plan_breakdown[plan_name]["count"] += 1
            plan_breakdown[plan_name]["revenue"] += price

        if b.get("active"):
            active_count += 1

        sid = str(b.get("seller_id"))
        if pstatus == "completed":
            seller_revenue[sid] = seller_revenue.get(sid, 0) + price

    top_sellers = sorted(seller_revenue.items(), key=lambda x: x[1], reverse=True)[:10]

    seller_names = {}
    if top_sellers:
        sids = [s[0] for s in top_sellers]
        try:
            ur = admin.table("users").select("id, full_name, email").in_("id", sids).execute()
            for u in ur.data or []:
                seller_names[str(u["id"])] = u.get("full_name") or u.get("email")
        except Exception:
            pass

    return {
        "window_days": days,
        "total_boosts_purchased": len(boosts),
        "total_revenue_ugx": total_revenue,
        "completed_purchases": completed_count,
        "active_boosts": active_count,
        "plan_breakdown": [
            {"plan": k, "count": v["count"], "revenue_ugx": v["revenue"]}
            for k, v in sorted(plan_breakdown.items(), key=lambda x: x[1]["revenue"], reverse=True)
        ],
        "top_sellers": [
            {
                "seller_id": sid,
                "name": seller_names.get(sid, "Unknown"),
                "revenue_ugx": rev,
            }
            for sid, rev in top_sellers
        ],
    }


@router.get("/boosts/plans")
async def admin_list_boost_plans() -> list[dict[str, Any]]:
    """Admin: list all boost plans."""
    admin = get_supabase_admin()
    r = admin.table("boost_plans").select("*").order("price_amount").execute()
    return r.data or []


@router.post("/boosts/plans")
async def admin_create_boost_plan(
    name: str = Query(...),
    duration_hours: int = Query(..., ge=1),
    price_amount: float = Query(..., ge=0),
    score_bonus: int = Query(0, ge=0),
    is_active: bool = Query(True),
) -> dict[str, Any]:
    """Admin: create a new boost plan."""
    admin = get_supabase_admin()
    payload = {
        "name": name,
        "duration_hours": duration_hours,
        "price_amount": price_amount,
        "score_bonus": score_bonus,
        "is_active": is_active,
    }
    r = admin.table("boost_plans").insert(payload).execute()
    if not r.data:
        return {"error": "Failed to create boost plan"}
    return r.data[0]


@router.patch("/boosts/plans/{plan_id}")
async def admin_update_boost_plan(
    plan_id: str,
    name: str | None = Query(None),
    duration_hours: int | None = Query(None, ge=1),
    price_amount: float | None = Query(None, ge=0),
    score_bonus: int | None = Query(None, ge=0),
    is_active: bool | None = Query(None),
) -> dict[str, Any]:
    """Admin: update a boost plan."""
    admin = get_supabase_admin()
    payload = {}
    if name is not None:
        payload["name"] = name
    if duration_hours is not None:
        payload["duration_hours"] = duration_hours
    if price_amount is not None:
        payload["price_amount"] = price_amount
    if score_bonus is not None:
        payload["score_bonus"] = score_bonus
    if is_active is not None:
        payload["is_active"] = is_active

    if not payload:
        return {"error": "No fields to update"}

    r = admin.table("boost_plans").update(payload).eq("id", plan_id).execute()
    if not r.data:
        return {"error": "Plan not found"}
    return r.data[0]
