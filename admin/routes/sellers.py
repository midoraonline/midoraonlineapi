from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query

from db.supabase import get_supabase_admin

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/sellers")
async def admin_list_sellers(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    status: str | None = Query(None, description="Filter by user status: active, suspended, blocked"),
) -> dict[str, Any]:
    """Admin: list all sellers with scores, listings count, and engagement."""
    admin = get_supabase_admin()
    offset = (page - 1) * limit

    q = (
        admin.table("users")
        .select("id, email, full_name, phone_number, user_role, status, created_at, last_seen_at", count="exact")
        .in_("user_role", ["merchant", "admin", "staff"])
    )
    if status:
        q = q.eq("status", status)

    r = q.range(offset, offset + limit - 1).order("created_at", desc=True).execute()
    total = r.count if hasattr(r, "count") and r.count is not None else len(r.data or [])
    total_pages = (total + limit - 1) // limit if limit else 0
    users_list = r.data or []

    user_ids = [u["id"] for u in users_list]

    shops_map: dict[str, list[dict]] = {}
    if user_ids:
        try:
            sr = (
                admin.table("shops")
                .select("id, name, slug, trust_score, fraud_score, seller_score, available_now, view_count, is_active")
                .in_("owner_id", user_ids)
                .execute()
            )
            for s in sr.data or []:
                owner_id = str(s.get("owner_id"))
                shops_map.setdefault(owner_id, []).append(s)
        except Exception as exc:
            logger.warning("shop lookup failed: %s", exc)

    product_counts: dict[str, int] = {}
    if shops_map:
        shop_ids = [s["id"] for shops in shops_map.values() for s in shops]
        try:
            pr = (
                admin.table("products")
                .select("shop_id")
                .in_("shop_id", shop_ids)
                .execute()
            )
            for p in pr.data or []:
                sid = str(p.get("shop_id"))
                product_counts[sid] = product_counts.get(sid, 0) + 1
        except Exception as exc:
            logger.warning("product count failed: %s", exc)

    orders_map: dict[str, float] = {}
    if shop_ids := [s["id"] for shops in shops_map.values() for s in shops]:
        try:
            or_ = (
                admin.table("orders")
                .select("shop_id, total_amount")
                .in_("shop_id", shop_ids)
                .neq("order_status", "cancelled")
                .execute()
            )
            for o in or_.data or []:
                sid = str(o.get("shop_id"))
                orders_map[sid] = orders_map.get(sid, 0) + float(o.get("total_amount", 0))
        except Exception as exc:
            logger.warning("order revenue failed: %s", exc)

    enriched = []
    for user in users_list:
        uid = str(user["id"])
        shops = shops_map.get(uid, [])
        total_listings = sum(product_counts.get(s["id"], 0) for s in shops)
        total_revenue = sum(orders_map.get(s["id"], 0.0) for s in shops)

        max_trust = max((s.get("trust_score") or 0) for s in shops) if shops else 0
        max_fraud = max((s.get("fraud_score") or 0) for s in shops) if shops else 0
        max_score = max((s.get("seller_score") or 0) for s in shops) if shops else 0

        enriched.append({
            **user,
            "shops": shops,
            "shop_count": len(shops),
            "total_listings": total_listings,
            "total_revenue_ugx": total_revenue,
            "trust_score": float(max_trust),
            "fraud_score": float(max_fraud),
            "seller_score": float(max_score),
        })

    return {
        "items": enriched,
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": total_pages,
    }


@router.patch("/sellers/{user_id}/status")
async def admin_update_seller_status(
    user_id: str,
    status: str = Query(..., description="New status: active, suspended, blocked"),
) -> dict[str, Any]:
    """Admin: suspend, un-suspend, or block a seller."""
    valid = {"active", "suspended", "blocked"}
    if status not in valid:
        return {"error": f"Invalid status. Must be one of: {', '.join(sorted(valid))}"}

    admin = get_supabase_admin()
    r = admin.table("users").update({"status": status}).eq("id", user_id).execute()
    if not r.data:
        return {"error": "User not found"}

    if status == "suspended" or status == "blocked":
        admin.table("shops").update({"is_active": False}).eq("owner_id", user_id).execute()

    return r.data[0]
