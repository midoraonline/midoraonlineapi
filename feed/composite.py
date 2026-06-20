"""Composite endpoint that returns feed data with shop details and boost
status embedded — eliminating the N+1 pattern on the frontend.
"""

from __future__ import annotations

import logging
from typing import Any

from db.supabase import get_supabase_admin
from shop.schemas import ProductResponse

logger = logging.getLogger(__name__)

MAX_CARDS = 72


def _safe_int(x: Any) -> int:
    try:
        return int(x)
    except (TypeError, ValueError):
        return 0


def _safe_float(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _coerce_images(image_urls: Any) -> list[str]:
    if isinstance(image_urls, list):
        return [str(x) for x in image_urls if x]
    if isinstance(image_urls, str):
        return [s.strip() for s in image_urls.split(",") if s.strip()]
    return []


def get_home_feed(
    limit: int = MAX_CARDS,
    page: int = 1,
    user_id: str | None = None,
) -> dict[str, Any]:
    """Return all 4 home-page feeds in one call with shop + boost data embedded.

    Pass `user_id` for personalised per-user feed scoring.
    `limit` controls the main algorithm feed size; sub-feeds are fixed.
    `page` controls which page of the full product pool is scored.
    """
    from feed.service import get_algorithm_feed, get_latest_feed
    from db.supabase import get_supabase_client

    admin = get_supabase_admin()
    client = get_supabase_client(None)

    offset = (page - 1) * limit
    pool_size = limit * page + 24  # fetch extra for the algorithm pool
    pool_size = min(pool_size, MAX_CARDS * 3)

    algorithm_raw = get_algorithm_feed(client, user_id=user_id, page=1, limit=pool_size)
    fresh_raw = get_latest_feed(client, limit=12)

    # Paginate the scored pool for the main algorithm feed
    algorithm_paged = algorithm_raw[offset:offset + limit] if offset < len(algorithm_raw) else []

    # Independent sub-feeds — sorted by their own metrics, not just algorithm top-N
    _SUB_FEED_LIMIT = 8
    try:
        trending_r = (
            admin.table("products")
            .select("id,shop_id,title,description,price_ugx,discount_price,discount_expires_at,image_urls,category,"
                    "item_type,is_published,status,listing_score,location_name,"
                    "created_at,view_count,stock_quantity")
            .eq("is_published", True)
            .eq("status", "active")
            .order("view_count", desc=True)
            .order("created_at", desc=True)
            .limit(_SUB_FEED_LIMIT)
            .execute()
        )
        trending_raw = [ProductResponse(**item) for item in (trending_r.data or [])]
    except Exception as exc:
        logger.warning("Trending feed query failed: %s", exc)
        trending_raw = algorithm_raw[:_SUB_FEED_LIMIT]

    try:
        premium_r = (
            admin.table("products")
            .select("id,shop_id,title,description,price_ugx,discount_price,discount_expires_at,image_urls,category,"
                    "item_type,is_published,status,listing_score,location_name,"
                    "created_at,view_count,stock_quantity")
            .eq("is_published", True)
            .eq("status", "active")
            .order("listing_score", desc=True)
            .order("created_at", desc=True)
            .limit(_SUB_FEED_LIMIT)
            .execute()
        )
        premium_raw = [ProductResponse(**item) for item in (premium_r.data or [])]
    except Exception as exc:
        logger.warning("Premium feed query failed: %s", exc)
        premium_raw = algorithm_raw[:_SUB_FEED_LIMIT]

    # Collect unique shop IDs from all feeds
    all_products = algorithm_paged + trending_raw + premium_raw + fresh_raw
    shop_ids = list({str(p.shop_id) for p in all_products if p.shop_id})

    # Batch-fetch shops (1 query instead of N)
    shops_map: dict[str, dict[str, Any]] = {}
    if shop_ids:
        try:
            shops_r = (
                admin.table("shops")
                .select(
                    "id,name,slug,logo_url,owner_id,whatsapp_number,"
                    "is_active,category,trust_score,available_now,location"
                )
                .in_("id", shop_ids)
                .execute()
            )
            for s in (shops_r.data or []):
                sid = str(s["id"])
                loc = s.get("location")
                shops_map[sid] = {
                    "id": sid,
                    "name": s.get("name", ""),
                    "slug": s.get("slug", ""),
                    "logo_url": s.get("logo_url"),
                    "owner_id": str(s.get("owner_id", "")) if s.get("owner_id") else None,
                    "whatsapp_number": s.get("whatsapp_number"),
                    "is_active": bool(s.get("is_active", False)),
                    "category": s.get("category"),
                    "trust_score": _safe_int(s.get("trust_score")),
                    "available_now": bool(s.get("available_now", False)),
                    "location": loc.get("display") if isinstance(loc, dict) else loc,
                }
        except Exception as exc:
            logger.warning("home feed batch shop fetch failed: %s", exc)

    product_ids = [str(p.id) for p in all_products if p.id]
    boosted_ids: set[str] = set()

    # Batch-fetch like counts from product_likes (1 query instead of N)
    like_counts: dict[str, int] = {}
    if product_ids:
        try:
            likes_r = (
                admin.table("product_likes")
                .select("product_id")
                .in_("product_id", product_ids)
                .execute()
            )
            counts_raw: dict[str, int] = {}
            for row in likes_r.data or []:
                pid = str(row.get("product_id"))
                counts_raw[pid] = counts_raw.get(pid, 0) + 1
            like_counts = {pid: counts_raw.get(pid, 0) for pid in product_ids}
        except Exception as exc:
            logger.warning("home feed batch like-count fetch failed: %s", exc)
    if product_ids:
        try:
            now_iso = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
            boosts_r = (
                admin.table("listing_boosts")
                .select("listing_id")
                .in_("listing_id", product_ids)
                .eq("active", True)
                .gte("ends_at", now_iso)
                .execute()
            )
            boosted_ids = {str(b["listing_id"]) for b in (boosts_r.data or []) if b.get("listing_id")}
        except Exception as exc:
            logger.warning("home feed batch boost fetch failed: %s", exc)

    # Batch-fetch product review averages (1 query instead of N)
    avg_ratings: dict[str, float] = {}
    if product_ids:
        try:
            rev_r = (
                admin.table("product_reviews")
                .select("product_id,rating")
                .in_("product_id", product_ids)
                .execute()
            )
            sums: dict[str, float] = {}
            counts: dict[str, int] = {}
            for row in rev_r.data or []:
                pid = str(row.get("product_id"))
                r = row.get("rating")
                if pid and r:
                    sums[pid] = sums.get(pid, 0) + float(r)
                    counts[pid] = counts.get(pid, 0) + 1
            for pid in product_ids:
                if counts.get(pid, 0) > 0:
                    avg_ratings[pid] = round(sums[pid] / counts[pid], 2)
                else:
                    avg_ratings[pid] = 0.0
        except Exception as exc:
            logger.warning("home feed batch rating fetch failed: %s", exc)

    def _embed(products: list) -> list[dict[str, Any]]:
        out = []
        for p in products:
            shop = shops_map.get(str(p.shop_id)) or {}
            imgs = _coerce_images(p.image_urls)
            out.append({
                "id": str(p.id),
                "shop_id": str(p.shop_id),
                "title": p.title,
                "slug": "",  # frontend computes from title+id
                "price_ugx": _safe_float(p.price_ugx),
                "discount_price": _safe_float(p.discount_price) if getattr(p, "discount_price", None) is not None else None,
                "discount_expires_at": getattr(p, "discount_expires_at", None),
                "image_urls": imgs,
                "primary_image": imgs[0] if imgs else None,
                "category": p.category,
                "item_type": p.item_type,
                "is_published": p.is_published,
                "view_count": _safe_int(p.view_count),
                "like_count": like_counts.get(str(p.id), 0),
                "listing_score": _safe_int(p.listing_score),
                "location_name": p.location_name,
                "created_at": p.created_at,
                "stock_quantity": int(getattr(p, "stock_quantity", 0) or 0),
                "updated_at": getattr(p, "updated_at", None),
                "shop": shop,
                "boosted": str(p.id) in boosted_ids,
                "average_rating": avg_ratings.get(str(p.id), 0.0),
            })
        return out

    return {
        "algorithm": _embed(algorithm_paged),
        "trending": _embed(trending_raw),
        "premium": _embed(premium_raw),
        "fresh": _embed(fresh_raw),
        "page": page,
        "limit": limit,
        "total": len(algorithm_raw),
    }
