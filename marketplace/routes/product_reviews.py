from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from core.security import get_current_user_id
from reviews.product_service import (
    ReviewWriteError,
    create_product_review,
    get_product_review_stats,
    get_user_product_review,
    list_product_reviews,
)

logger = logging.getLogger(__name__)

router = APIRouter()


class ReviewWrite(BaseModel):
    rating: int = Field(..., ge=1, le=5)
    comment: str | None = Field(default=None, max_length=4000)


def _reject(exc: ReviewWriteError) -> HTTPException:
    return HTTPException(
        status_code=exc.status,
        detail={"detail": str(exc), "code": exc.code},
    )


async def _rating_from_request(
    request: Request,
    rating: int | None,
    comment: str | None,
) -> tuple[int, str | None]:
    """Accept JSON `{rating, comment}` or query params.

    The current web client posts `?rating=&comment=` with a body of `{}`.
    An empty JSON object keeps the query values.
    """
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            raw = await request.json()
        except Exception:
            raw = None
        if isinstance(raw, dict) and ("rating" in raw or "comment" in raw):
            try:
                body = ReviewWrite.model_validate(
                    {"rating": raw.get("rating", rating), "comment": raw.get("comment", comment)}
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=422,
                    detail={"detail": "Rating must be between 1 and 5.", "code": "invalid_rating"},
                ) from exc
            return body.rating, body.comment
    if rating is None:
        raise HTTPException(
            status_code=422,
            detail={"detail": "Rating is required.", "code": "rating_required"},
        )
    if rating < 1 or rating > 5:
        raise HTTPException(
            status_code=422,
            detail={"detail": "Rating must be between 1 and 5.", "code": "invalid_rating"},
        )
    return rating, comment


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
    request: Request,
    rating: int | None = Query(None),
    comment: str | None = Query(None),
    current_user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """Leave or update a review.

    JSON body `{ "rating": 1-5, "comment": "..." }` or the same fields as
    query parameters. A second review from the same user updates the first.
    """
    score, text = await _rating_from_request(request, rating, comment)
    try:
        review = create_product_review(
            product_id=product_id,
            user_id=current_user_id,
            rating=score,
            comment=text,
        )
    except ReviewWriteError as exc:
        logger.warning(
            "product review rejected product=%s user=%s code=%s",
            product_id,
            current_user_id,
            exc.code,
        )
        raise _reject(exc) from exc
    except Exception as exc:
        logger.exception(
            "product review failed product=%s user=%s",
            product_id,
            current_user_id,
        )
        raise HTTPException(
            status_code=503,
            detail={"detail": "Could not save this review.", "code": "review_write_failed"},
        ) from exc
    if not review:
        raise HTTPException(
            status_code=503,
            detail={"detail": "Could not save this review.", "code": "review_write_failed"},
        )
    return review


@router.get("/{product_id}/reviews/stats")
async def get_product_review_stats_endpoint(
    product_id: str,
) -> dict[str, Any]:
    """Get aggregated review stats for a product."""
    return get_product_review_stats(product_id)
