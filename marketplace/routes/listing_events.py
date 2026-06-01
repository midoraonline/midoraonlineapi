from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query

from core.security import get_optional_user_id, get_current_user_id
from db.supabase import get_supabase_admin

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/{product_id}/events")
async def record_listing_event(
    product_id: str,
    event_type: str = Query(..., description="Event type: viewed, whatsapp_clicked, call_clicked, saved, shared, reported, updated"),
    current_user_id: str | None = Depends(get_optional_user_id),
    ip_address: str | None = Query(None),
    device_hash: str | None = Query(None),
) -> dict[str, Any]:
    """Record a listing event (view, click, share, report, etc.)."""
    valid_types = {"viewed", "whatsapp_clicked", "call_clicked", "saved", "shared", "reported", "updated"}
    if event_type not in valid_types:
        return {"error": f"Invalid event_type. Must be one of: {', '.join(sorted(valid_types))}"}

    admin = get_supabase_admin()

    product_r = admin.table("products").select("shop_id").eq("id", product_id).execute()
    if not product_r.data:
        return {"error": "Product not found"}
    product = product_r.data[0]
    shop_id = str(product.get("shop_id", ""))

    shop_r = admin.table("shops").select("owner_id").eq("id", shop_id).execute()
    seller_id = str(shop_r.data[0]["owner_id"]) if shop_r.data else None

    payload = {
        "listing_id": product_id,
        "seller_id": seller_id,
        "buyer_id": current_user_id,
        "session_id": None,
        "event_type": event_type,
        "ip_address": ip_address,
        "device_hash": device_hash,
        "metadata": {},
    }
    r = admin.table("listing_events").insert(payload).execute()

    return r.data[0] if r.data else {"status": "recorded"}


@router.get("/{product_id}/events/stats")
async def get_listing_event_stats(product_id: str) -> dict[str, Any]:
    """Get aggregated event counts for a listing."""
    admin = get_supabase_admin()
    stats = {"views": 0, "whatsapp_clicks": 0, "call_clicks": 0, "saves": 0, "shares": 0, "reports": 0}

    try:
        r = (
            admin.table("listing_events")
            .select("event_type")
            .eq("listing_id", product_id)
            .execute()
        )
        for ev in r.data or []:
            et = ev.get("event_type")
            if et == "viewed":
                stats["views"] += 1
            elif et == "whatsapp_clicked":
                stats["whatsapp_clicks"] += 1
            elif et == "call_clicked":
                stats["call_clicks"] += 1
            elif et == "saved":
                stats["saves"] += 1
            elif et == "shared":
                stats["shares"] += 1
            elif et == "reported":
                stats["reports"] += 1
    except Exception as exc:
        logger.warning("get_listing_event_stats(%s) failed: %s", product_id, exc)

    return stats
