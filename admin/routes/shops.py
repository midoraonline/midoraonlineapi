from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from core.schemas import PaginationParams
from admin import service as admin_service
from db.supabase import get_supabase_admin

router = APIRouter()


@router.get("/")
async def list_all_shops(params: Annotated[PaginationParams, Depends()]):
    return admin_service.list_all_shops(page=params.page, limit=params.limit)


@router.patch("/{shop_id}/active")
async def set_shop_active(shop_id: str, is_active: bool = True):
    result = admin_service.set_shop_active(shop_id, is_active)
    if not result:
        raise HTTPException(status_code=404, detail="Shop not found")
    return result


@router.get("/submitted")
async def list_submitted_verifications(
    stage: int | None = Query(None, description="Filter by stage: 2=Identity, 3=Business"),
    status: str = Query("pending", description="Filter by stage status: pending, verified, rejected"),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    """Admin: list all verification submissions, optionally filtered by stage and status.

    Each item includes shop info + full stage breakdown from metadata.
    """
    client = get_supabase_admin()

    q = (
        client.table("shop_verifications")
        .select("*, shops(id, name, slug, owner_id, shop_email, is_active, created_at, logo_url)")
        .order("requested_at", desc=True)
        .limit(limit)
    )

    # Filter by top-level status (pending catches both stage 2 and 3 in-progress)
    if status != "all":
        q = q.eq("status", status)

    r = q.execute()
    items: list[dict[str, Any]] = []

    for row in r.data or []:
        meta: dict[str, Any] = row.get("metadata") or {}
        badges: list[str] = meta.get("badges") or ["shop_listed"]

        stage2_status = meta.get("stage2_status", "unverified")
        stage3_status = meta.get("stage3_status", "unverified")

        # Stage filter
        if stage == 2 and stage2_status != status:
            continue
        if stage == 3 and stage3_status != status:
            continue

        shop_data = row.get("shops") or {}
        items.append({
            "id": row.get("id"),
            "shop_id": row.get("shop_id"),
            "shop_name": shop_data.get("name"),
            "shop_slug": shop_data.get("slug"),
            "shop_logo_url": shop_data.get("logo_url"),
            "owner_id": shop_data.get("owner_id"),
            "merchant_email": shop_data.get("shop_email"),
            "shop_is_active": shop_data.get("is_active"),
            "shop_created_at": shop_data.get("created_at"),
            "badges": badges,
            "stage2_status": stage2_status,
            "stage3_status": stage3_status,
            "stage2_requested_at": meta.get("stage2_requested_at"),
            "stage3_requested_at": meta.get("stage3_requested_at"),
            "stage2_notes": meta.get("stage2_notes"),
            "stage3_notes": meta.get("stage3_notes"),
            "stage2_docs": meta.get("stage2_docs"),
            "stage3_docs": meta.get("stage3_docs"),
            "overall_status": row.get("status"),
            "requested_at": row.get("requested_at"),
            "reviewed_at": row.get("reviewed_at"),
        })

    return {"items": items, "total": len(items)}
