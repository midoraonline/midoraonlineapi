from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query

from core.security import get_current_user_id, get_optional_user_id
from db.supabase import get_supabase_admin
from ranking.lead_service import (
    record_lead_event,
    list_leads_for_seller,
    get_lead_stats_for_seller,
    update_lead_status,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/{shop_id}/products/{product_id}/leads")
async def create_lead(
    shop_id: str,
    product_id: str,
    source: str = Query("whatsapp", description="Lead source: whatsapp, call, contact_form, email"),
    current_user_id: str | None = Depends(get_optional_user_id),
) -> dict[str, Any]:
    """Record a lead event (buyer contacted seller about a listing)."""
    valid_sources = {"whatsapp", "call", "contact_form", "email"}
    if source not in valid_sources:
        return {"error": f"Invalid source. Must be one of: {', '.join(sorted(valid_sources))}"}

    admin = get_supabase_admin()
    shop_r = admin.table("shops").select("owner_id").eq("id", shop_id).execute()
    if not shop_r.data:
        return {"error": "Shop not found"}
    seller_id = str(shop_r.data[0]["owner_id"])

    try:
        lead = record_lead_event(
            listing_id=product_id,
            seller_id=seller_id,
            buyer_id=current_user_id,
            source=source,
            metadata={"shop_id": shop_id},
        )
        if lead:
            return lead
        return {"status": "duplicate", "message": "Lead already recorded"}
    except Exception as exc:
        logger.warning("create_lead failed: %s", exc)
        return {"error": "Failed to record lead"}


@router.get("/{shop_id}/leads/stats")
async def get_shop_lead_stats(
    shop_id: str,
    current_user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """Seller dashboard: get lead stats for a shop."""
    admin = get_supabase_admin()
    shop_r = admin.table("shops").select("owner_id").eq("id", shop_id).execute()
    if not shop_r.data:
        return {"error": "Shop not found"}
    if str(shop_r.data[0]["owner_id"]) != current_user_id:
        return {"error": "You do not own this shop"}

    return get_lead_stats_for_seller(current_user_id)


@router.get("/{shop_id}/leads")
async def get_shop_leads(
    shop_id: str,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    status: str | None = Query(None),
    current_user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """Seller dashboard: paginated list of leads for a shop."""
    admin = get_supabase_admin()
    shop_r = admin.table("shops").select("owner_id").eq("id", shop_id).execute()
    if not shop_r.data:
        return {"error": "Shop not found"}
    if str(shop_r.data[0]["owner_id"]) != current_user_id:
        return {"error": "You do not own this shop"}

    return list_leads_for_seller(
        seller_id=current_user_id,
        page=page,
        limit=limit,
        status=status,
    )


@router.patch("/{shop_id}/leads/{lead_id}/status")
async def change_lead_status(
    shop_id: str,
    lead_id: str,
    status: str = Query(..., description="New status: responded, ignored, closed"),
    current_user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """Seller: update lead status."""
    valid = {"responded", "ignored", "closed"}
    if status not in valid:
        return {"error": f"Invalid status. Must be one of: {', '.join(sorted(valid))}"}

    admin = get_supabase_admin()
    shop_r = admin.table("shops").select("owner_id").eq("id", shop_id).execute()
    if not shop_r.data:
        return {"error": "Shop not found"}
    if str(shop_r.data[0]["owner_id"]) != current_user_id:
        return {"error": "You do not own this shop"}

    result = update_lead_status(lead_id, status)
    if not result:
        return {"error": "Lead not found"}
    return result
