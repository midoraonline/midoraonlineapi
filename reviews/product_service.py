from __future__ import annotations

import logging
from typing import Any

from db.supabase import get_supabase_admin

logger = logging.getLogger(__name__)


def create_product_review(
    product_id: str,
    user_id: str,
    rating: int,
    comment: str | None = None,
) -> dict | None:
    """Create a product review (one per user per product)."""
    if rating < 1 or rating > 5:
        raise ValueError("Rating must be between 1 and 5")

    admin = get_supabase_admin()

    existing = (
        admin.table("product_reviews")
        .select("id")
        .eq("product_id", product_id)
        .eq("user_id", user_id)
        .execute()
    )
    if existing.data:
        raise ValueError("You have already reviewed this product")

    payload = {
        "product_id": product_id,
        "user_id": user_id,
        "rating": rating,
        "comment": comment,
    }
    r = admin.table("product_reviews").insert(payload).execute()
    if not r.data:
        return None
    return r.data[0]


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
            .execute()
        )
        ratings = [row["rating"] for row in (r.data or []) if row.get("rating")]
        count = len(ratings)
        avg = round(sum(ratings) / count, 2) if count else 0.0
        distribution = {i: ratings.count(i) for i in range(1, 6)}
        return {
            "total_reviews": count,
            "average_rating": avg,
            "distribution": distribution,
        }
    except Exception as exc:
        logger.warning("get_product_review_stats(%s) failed: %s", product_id, exc)
        return {"total_reviews": 0, "average_rating": 0.0, "distribution": {}}
