from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query

from db.supabase import get_supabase_admin
from ranking.fraud_service import resolve_fraud_flag, list_fraud_flags, get_seller_fraud_history

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/fraud-flags")
async def admin_list_fraud(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    resolved: bool | None = Query(None),
    severity: str | None = Query(None),
) -> dict[str, Any]:
    """Admin: list fraud flags with filters."""
    return list_fraud_flags(page=page, limit=limit, resolved=resolved, severity=severity)


@router.get("/fraud-flags/seller/{seller_id}")
async def admin_seller_fraud_history(seller_id: str) -> list[dict[str, Any]]:
    """Admin: view fraud history for a specific seller."""
    return get_seller_fraud_history(seller_id)


@router.post("/fraud-flags/{flag_id}/resolve")
async def admin_resolve_fraud_flag(
    flag_id: str,
    notes: str | None = Query(None),
) -> dict[str, Any]:
    """Admin: mark a fraud flag as resolved."""
    result = resolve_fraud_flag(flag_id, notes=notes)
    if not result:
        return {"error": "Flag not found"}
    return result


@router.post("/fraud-flags/{flag_id}/penalize")
async def admin_penalize_seller(
    flag_id: str,
    seller_id: str = Query(...),
) -> dict[str, Any]:
    """Admin: penalize a seller by increasing fraud score."""
    admin = get_supabase_admin()

    flag = admin.table("fraud_flags").select("*").eq("id", flag_id).execute()
    if not flag.data:
        return {"error": "Flag not found"}

    admin.table("fraud_flags").update({"resolved": True}).eq("id", flag_id).execute()

    shops = admin.table("shops").select("id, fraud_score").eq("owner_id", seller_id).execute()
    for shop in (shops.data or []):
        current = float(shop.get("fraud_score", 0))
        new_score = round(min(current + 0.5, 5.0), 2)
        admin.table("shops").update({"fraud_score": new_score}).eq("id", shop["id"]).execute()

    from ranking.service import calculate_shop_seller_score
    for shop in (shops.data or []):
        calculate_shop_seller_score(str(shop["id"]))

    return {"status": "penalized", "seller_id": seller_id}
