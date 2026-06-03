from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query

from core.security import get_current_user_id
from db.supabase import get_supabase_admin

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/listings")
async def admin_list_listings(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    status: str | None = Query(None),
    category: str | None = Query(None),
    search: str | None = Query(None),
) -> dict[str, Any]:
    """Admin: list all products with filters and enrichment."""
    admin = get_supabase_admin()
    offset = (page - 1) * limit

    q = (
        admin.table("products")
        .select(
            "id, shop_id, title, description, price_ugx, image_urls, category, "
            "item_type, status, listing_score, location_name, is_published, "
            "view_count, created_at, reviewed_by, reviewed_at, review_notes",
            count="exact",
        )
    )
    if status:
        q = q.eq("status", status)
    if category:
        q = q.eq("category", category)
    if search:
        q = q.or_(f"title.ilike.%{search}%,description.ilike.%{search}%")

    r = q.range(offset, offset + limit - 1).order("created_at", desc=True).execute()
    total = r.count if hasattr(r, "count") and r.count is not None else len(r.data or [])
    total_pages = (total + limit - 1) // limit if limit else 0
    items = r.data or []

    shop_ids = list({str(row["shop_id"]) for row in items if row.get("shop_id")})
    shops_map: dict[str, dict] = {}
    if shop_ids:
        try:
            sr = admin.table("shops").select("id, name, slug, owner_id").in_("id", shop_ids).execute()
            for s in sr.data or []:
                shops_map[str(s["id"])] = s
        except Exception as exc:
            logger.warning("shop lookup failed: %s", exc)

    report_counts: dict[str, int] = {}
    try:
        rr = (
            admin.table("listing_events")
            .select("listing_id")
            .eq("event_type", "reported")
            .execute()
        )
        for ev in rr.data or []:
            lid = str(ev.get("listing_id"))
            report_counts[lid] = report_counts.get(lid, 0) + 1
    except Exception:
        pass

    enriched = []
    for row in items:
        sid = str(row.get("shop_id"))
        shop = shops_map.get(sid, {})
        enriched.append({
            **row,
            "shop_name": shop.get("name"),
            "shop_slug": shop.get("slug"),
            "owner_id": shop.get("owner_id"),
            "reports_count": report_counts.get(str(row["id"]), 0),
        })

    return {
        "items": enriched,
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": total_pages,
    }


@router.patch("/listings/{listing_id}/status")
async def admin_update_listing_status(
    listing_id: str,
    status: str = Query(..., description="New status: active, hidden, rejected, pending_review"),
) -> dict[str, Any]:
    """Admin: update listing status (approve, reject, hide, etc.)."""
    valid_statuses = {"active", "hidden", "rejected", "pending_review"}
    if status not in valid_statuses:
        return {"error": f"Invalid status. Must be one of: {', '.join(sorted(valid_statuses))}"}

    admin = get_supabase_admin()
    now = datetime.now(timezone.utc).isoformat()
    r = (
        admin.table("products")
        .update({"status": status, "reviewed_at": now})
        .eq("id", listing_id)
        .execute()
    )
    if not r.data:
        return {"error": "Listing not found"}
    return r.data[0]


@router.post("/listings/{listing_id}/review")
async def admin_review_listing(
    listing_id: str,
    action: str = Query(..., description="approve or reject"),
    notes: str | None = Query(None),
    admin_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """Approve or reject a pending listing with review notes."""
    if action not in {"approve", "reject"}:
        return {"error": "Action must be 'approve' or 'reject'"}

    new_status = "active" if action == "approve" else "rejected"
    admin = get_supabase_admin()
    now = datetime.now(timezone.utc).isoformat()

    r = (
        admin.table("products")
        .update({
            "status": new_status,
            "reviewed_by": admin_id,
            "reviewed_at": now,
            "review_notes": notes,
        })
        .eq("id", listing_id)
        .execute()
    )
    if not r.data:
        return {"error": "Listing not found"}
    return r.data[0]
