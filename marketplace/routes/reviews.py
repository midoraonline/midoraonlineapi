from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from core.security import get_current_user_id
from db.supabase import get_supabase_admin
from reviews.service import (
    create_seller_review,
    list_seller_reviews,
    get_user_review_for_seller,
    get_seller_review_stats,
)

router = APIRouter()


@router.get("/{shop_id}/reviews")
async def get_shop_reviews(
    shop_id: str,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    """Get reviews for a shop (by shop_id -> owner_id)."""
    admin = get_supabase_admin()
    shop_r = admin.table("shops").select("owner_id").eq("id", shop_id).execute()
    if not shop_r.data:
        return {"error": "Shop not found"}
    seller_id = str(shop_r.data[0]["owner_id"])
    return list_seller_reviews(seller_id, page=page, limit=limit)


@router.get("/{shop_id}/reviews/mine")
async def get_my_shop_review(
    shop_id: str,
    current_user_id: str = Depends(get_current_user_id),
) -> dict[str, Any] | None:
    """Check if current user has reviewed this shop's seller."""
    admin = get_supabase_admin()
    shop_r = admin.table("shops").select("owner_id").eq("id", shop_id).execute()
    if not shop_r.data:
        return None
    seller_id = str(shop_r.data[0]["owner_id"])
    return get_user_review_for_seller(seller_id, current_user_id)


@router.post("/{shop_id}/reviews")
async def create_shop_review(
    shop_id: str,
    rating: int = Query(..., ge=1, le=5),
    comment: str | None = Query(None),
    current_user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """Leave a review for a shop's seller."""
    admin = get_supabase_admin()
    shop_r = admin.table("shops").select("owner_id").eq("id", shop_id).execute()
    if not shop_r.data:
        return {"error": "Shop not found"}
    seller_id = str(shop_r.data[0]["owner_id"])

    if seller_id == current_user_id:
        return {"error": "You cannot review your own shop"}

    try:
        review = create_seller_review(
            seller_id=seller_id,
            buyer_id=current_user_id,
            rating=rating,
            comment=comment,
        )
        if not review:
            return {"error": "Failed to create review"}
        return review
    except ValueError as e:
        return {"error": str(e)}


@router.get("/{shop_id}/reviews/stats")
async def get_shop_review_stats_endpoint(shop_id: str) -> dict[str, Any]:
    """Get aggregated review stats for a shop's seller."""
    admin = get_supabase_admin()
    shop_r = admin.table("shops").select("owner_id").eq("id", shop_id).execute()
    if not shop_r.data:
        return {"error": "Shop not found"}
    seller_id = str(shop_r.data[0]["owner_id"])
    return get_seller_review_stats(seller_id)
