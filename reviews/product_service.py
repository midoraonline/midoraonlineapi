from __future__ import annotations

import logging
from typing import Any

from postgrest.exceptions import APIError

from db.supabase import get_supabase_admin

logger = logging.getLogger(__name__)


class ReviewWriteError(Exception):
    def __init__(self, message: str, code: str, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


def _api_text(exc: APIError) -> str:
    return " ".join(
        str(part or "")
        for part in (getattr(exc, "message", ""), getattr(exc, "details", ""), getattr(exc, "hint", ""))
    )


def _raise_db(exc: APIError) -> None:
    logger.exception("product review write failed: %s", _api_text(exc))
    code = str(getattr(exc, "code", "") or "")
    text = _api_text(exc).lower()
    if code == "23505":
        raise ReviewWriteError(
            "You have already reviewed this product.",
            "duplicate_review",
            409,
        ) from exc
    if code == "23514" or "rating" in text and "check" in text:
        raise ReviewWriteError("Rating must be between 1 and 5.", "invalid_rating", 422) from exc
    if code == "23503":
        raise ReviewWriteError("That listing was not found.", "product_not_found", 404) from exc
    if "average_rating" in text or "rating_count" in text:
        raise ReviewWriteError(
            "Saving a review failed because a database trigger still updates removed rating columns. Apply migration 050.",
            "review_trigger",
            503,
        ) from exc
    if code in {"23502", "42703"}:
        raise ReviewWriteError(
            "Saving a review failed because the reviews table is missing a column or default. Apply migration 050.",
            "review_schema",
            503,
        ) from exc
    raise ReviewWriteError("Could not save this review.", "review_write_failed", 503) from exc


def _update_review(admin: Any, product_id: str, user_id: str, rating: int, comment: str | None) -> dict | None:
    updated = (
        admin.table("product_reviews")
        .update({"rating": rating, "comment": comment})
        .eq("product_id", product_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not updated.data:
        return None
    return updated.data[0]


def create_product_review(
    product_id: str,
    user_id: str,
    rating: int,
    comment: str | None = None,
    *,
    client: Any | None = None,
) -> dict | None:
    """Create or update the caller's review for a product."""
    if rating < 1 or rating > 5:
        raise ReviewWriteError("Rating must be between 1 and 5.", "invalid_rating", 422)

    admin = client or get_supabase_admin()
    try:
        existing = (
            admin.table("product_reviews")
            .select("id")
            .eq("product_id", product_id)
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
    except APIError as exc:
        _raise_db(exc)

    if existing.data:
        try:
            return _update_review(admin, product_id, user_id, rating, comment)
        except APIError as exc:
            _raise_db(exc)

    payload = {
        "product_id": product_id,
        "user_id": user_id,
        "rating": rating,
        "comment": comment,
    }
    try:
        created = admin.table("product_reviews").insert(payload).execute()
    except APIError as exc:
        if str(getattr(exc, "code", "") or "") == "23505":
            logger.warning("product review insert raced unique constraint; updating product=%s user=%s", product_id, user_id)
            try:
                return _update_review(admin, product_id, user_id, rating, comment)
            except APIError as update_exc:
                _raise_db(update_exc)
        _raise_db(exc)
    if not created.data:
        return None
    return created.data[0]


def list_product_reviews(
    product_id: str,
    page: int = 1,
    limit: int = 20,
) -> dict:
    """Paginated list of reviews for a product."""
    admin = get_supabase_admin()
    limit = min(limit, 100)
    offset = (page - 1) * limit

    q = (
        admin.table("product_reviews")
        .select("*, users!product_reviews_user_id_fkey(full_name)", count="exact")
        .eq("product_id", product_id)
    )

    r = q.range(offset, offset + limit - 1).order("created_at", desc=True).execute()
    total = r.count if hasattr(r, "count") and r.count is not None else len(r.data or [])
    total_pages = (total + limit - 1) // limit if limit else 0

    return {
        "items": r.data or [],
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": total_pages,
    }


def get_user_product_review(product_id: str, user_id: str) -> dict | None:
    """Get a specific user's review for a product (if exists)."""
    admin = get_supabase_admin()
    r = (
        admin.table("product_reviews")
        .select("*")
        .eq("product_id", product_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    return r.data[0] if r.data else None


def get_product_review_stats(product_id: str) -> dict:
    """Aggregated review stats for a product."""
    admin = get_supabase_admin()
    try:
        r = (
            admin.table("product_reviews")
            .select("rating")
            .eq("product_id", product_id)
            .limit(5000)
            .execute()
        )
        ratings = [int(row["rating"]) for row in (r.data or []) if row.get("rating")]
        count = len(ratings)
        avg = round(sum(ratings) / count, 2) if count else 0.0
        distribution = {i: 0 for i in range(1, 6)}
        for rating in ratings:
            if 1 <= rating <= 5:
                distribution[rating] += 1
        return {
            "total_reviews": count,
            "average_rating": avg,
            "distribution": distribution,
        }
    except Exception as exc:
        logger.warning("get_product_review_stats(%s) failed: %s", product_id, exc)
        return {"total_reviews": 0, "average_rating": 0.0, "distribution": {}}
