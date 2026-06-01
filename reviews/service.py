from __future__ import annotations

import logging
from typing import Any

from db.supabase import get_supabase_admin

logger = logging.getLogger(__name__)


def create_seller_review(
    seller_id: str,
    buyer_id: str,
    rating: int,
    comment: str | None = None,
) -> dict | None:
    """Create a review for a seller by a buyer."""
    if rating < 1 or rating > 5:
        raise ValueError("Rating must be between 1 and 5")

    admin = get_supabase_admin()

    existing = (
        admin.table("seller_reviews")
        .select("id")
        .eq("seller_id", seller_id)
        .eq("buyer_id", buyer_id)
        .execute()
    )
    if existing.data:
        raise ValueError("You have already reviewed this seller")

    payload = {
        "seller_id": seller_id,
        "buyer_id": buyer_id,
        "rating": rating,
        "comment": comment,
    }
    r = admin.table("seller_reviews").insert(payload).execute()
    if not r.data:
        return None

    from ranking.service import recalculate_seller_score_from_reviews
    recalculate_seller_score_from_reviews(seller_id)

    return r.data[0]


def list_seller_reviews(
    seller_id: str,
    page: int = 1,
    limit: int = 20,
) -> dict:
    """Paginated list of reviews for a seller."""
    admin = get_supabase_admin()
    limit = min(limit, 100)
    offset = (page - 1) * limit

    q = (
        admin.table("seller_reviews")
        .select("*, users!seller_reviews_buyer_id_fkey(full_name, avatar_url)", count="exact")
        .eq("seller_id", seller_id)
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


def get_user_review_for_seller(seller_id: str, buyer_id: str) -> dict | None:
    """Get a specific buyer's review for a seller (if exists)."""
    admin = get_supabase_admin()
    r = (
        admin.table("seller_reviews")
        .select("*")
        .eq("seller_id", seller_id)
        .eq("buyer_id", buyer_id)
        .limit(1)
        .execute()
    )
    return r.data[0] if r.data else None


def get_seller_review_stats(seller_id: str) -> dict:
    """Aggregated review stats for a seller."""
    admin = get_supabase_admin()
    try:
        r = (
            admin.table("seller_reviews")
            .select("rating")
            .eq("seller_id", seller_id)
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
        logger.warning("get_seller_review_stats(%s) failed: %s", seller_id, exc)
        return {"total_reviews": 0, "average_rating": 0.0, "distribution": {}}
