from __future__ import annotations

import logging
from typing import Any

from db.supabase import get_supabase_admin

logger = logging.getLogger(__name__)


def calculate_listing_score(product_id: str) -> int:
    """Recompute listing_score for a product using the DB function."""
    admin = get_supabase_admin()
    try:
        r = admin.rpc("recalculate_product_listing_score", {"p_product_id": product_id}).execute()
        if r.data is not None:
            return int(r.data)
    except Exception as exc:
        logger.warning("recalculate_product_listing_score(%s) failed: %s", product_id, exc)
    return 0


def calculate_shop_seller_score(shop_id: str) -> float:
    """Recompute seller_score for a shop using the DB function."""
    admin = get_supabase_admin()
    try:
        r = admin.rpc("recalculate_shop_seller_score", {"p_shop_id": shop_id}).execute()
        if r.data is not None:
            return float(r.data)
    except Exception as exc:
        logger.warning("recalculate_shop_seller_score(%s) failed: %s", shop_id, exc)
    return 0.0


def recalculate_seller_score_from_reviews(seller_id: str) -> None:
    """Recalculate trust_score for all shops owned by a seller based on reviews."""
    admin = get_supabase_admin()
    try:
        rr = (
            admin.table("seller_reviews")
            .select("rating")
            .eq("seller_id", seller_id)
            .execute()
        )
        ratings = [r["rating"] for r in (rr.data or []) if r.get("rating")]
        if not ratings:
            new_trust = 0.0
        else:
            new_trust = round(sum(ratings) / len(ratings), 2)

        shops_r = (
            admin.table("shops")
            .select("id")
            .eq("owner_id", seller_id)
            .execute()
        )
        for shop in (shops_r.data or []):
            sid = shop.get("id")
            if sid:
                admin.table("shops").update({"trust_score": new_trust}).eq("id", sid).execute()
                calculate_shop_seller_score(str(sid))
    except Exception as exc:
        logger.warning("recalculate_seller_score_from_reviews(%s) failed: %s", seller_id, exc)
