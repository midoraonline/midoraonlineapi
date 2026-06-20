from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from core.security import get_current_user_id
from reviews.product_service import (
    create_product_review,
    list_product_reviews,
    get_user_product_review,
    get_product_review_stats,
)

router = APIRouter()


@router.get("/{product_id}/reviews")
async def get_product_reviews_endpoint(
    product_id: str,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    """Get paginated reviews for a product."""
    return list_product_reviews(product_id, page=page, limit=limit)


@router.get("/{product_id}/reviews/mine")
async def get_my_product_review(
    product_id: str,
    current_user_id: str = Depends(get_current_user_id),
) -> dict[str, Any] | None:
    """Get current user's review for this product."""
    return get_user_product_review(product_id, current_user_id)


@router.post("/{product_id}/reviews")
async def create_product_review_endpoint(
    product_id: str,
    rating: int = Query(..., ge=1, le=5),
    comment: str | None = Query(None),
    current_user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """Leave a review for a product."""
    try:
        review = create_product_review(
            product_id=product_id,
            user_id=current_user_id,
            rating=rating,
            comment=comment,
        )
        if not review:
            return {"error": "Failed to create review"}
        return review
    except ValueError as e:
        return {"error": str(e)}


@router.get("/{product_id}/reviews/stats")
async def get_product_review_stats_endpoint(
    product_id: str,
) -> dict[str, Any]:
    """Get aggregated review stats for a product."""
    return get_product_review_stats(product_id)
