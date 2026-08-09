"""Composite endpoint that returns feed data with shop details and boost
status embedded — eliminating the N+1 pattern on the frontend.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from db.supabase import get_supabase_admin
from shop.schemas import ProductResponse

logger = logging.getLogger(__name__)

MAX_CARDS = 72
_SUB_FEED_LIMIT = 8
_CARD_SELECT = (
    "id,shop_id,title,price_ugx,discount_price,discount_expires_at,image_urls,category,"
    "item_type,is_published,status,listing_score,location_name,is_negotiable,"
    "listing_meta,created_at,view_count,stock_quantity"
)

# Short process-local cache for anonymous public sub-feeds (trending/premium).
_GUEST_SUBFEED_TTL_S = 60.0
_guest_subfeed_cache: dict[str, tuple[float, list[ProductResponse]]] = {}


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


def _cached_guest_subfeed(key: str, loader) -> list[ProductResponse]:
    now = time.monotonic()
    hit = _guest_subfeed_cache.get(key)
    if hit and (now - hit[0]) < _GUEST_SUBFEED_TTL_S:
        return list(hit[1])
    rows = loader()
    _guest_subfeed_cache[key] = (now, list(rows))
    return rows


def _query_ordered_products(
    admin: Any,
    *,
    order_col: str,
    limit: int,
    exclude_set: set[str],
) -> list[ProductResponse]:
    from db.supabase import get_supabase_admin, with_supabase_retry

    def _run():
        db = get_supabase_admin()
        return (
            db.table("products")
            .select(_CARD_SELECT)
            .eq("is_published", True)
            .eq("status", "active")
            .order(order_col, desc=True)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )

    try:
        r = with_supabase_retry(_run, label=f"subfeed:{order_col}")
        return [
            ProductResponse(**{**item, "description": item.get("description")})
            for item in (r.data or [])
            if str(item.get("id")) not in exclude_set
        ][:_SUB_FEED_LIMIT]
    except Exception as exc:
        logger.warning("subfeed query (%s) failed: %s", order_col, exc)
        return []


def get_home_feed(
    limit: int = MAX_CARDS,
    page: int = 1,
    user_id: str | None = None,
    exclude_ids: list[str] | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Return home-page feeds with shop + boost data embedded.

    Pass `user_id` for personalised per-user feed scoring.
    `limit` controls the main algorithm feed size; sub-feeds are fixed.
    `page` controls which page of the full product pool is scored.
    `exclude_ids` hides listings the client has already rendered this
    session; `session_id` enables fatigue suppression for anonymous viewers.
    """
    from feed.service import get_algorithm_feed, get_latest_feed
    from db.supabase import get_supabase_admin

    admin = get_supabase_admin()
    is_guest = not user_id
    exclude_set = set(exclude_ids or [])
    # Sub-feeds only on the true first paint — not on load-more continuation
    # (exclude_ids set) which often still sends page=1.
    want_subfeeds = page <= 1 and not exclude_set

    # Prefer fewer concurrent PostgREST calls — local DNS blips are amplified
    # when algorithm + trending + premium + fresh all resolve at once.
    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_algo = pool.submit(
            get_algorithm_feed,
            admin,
            user_id=user_id,
            page=page,
            limit=limit,
            exclude_ids=exclude_ids,
            session_id=session_id,
        )
        fut_fresh = (
            pool.submit(get_latest_feed, admin, 12) if want_subfeeds else None
        )
        fut_trending = None
        fut_premium = None
        if want_subfeeds:
            if is_guest:
                fut_trending = pool.submit(
                    _cached_guest_subfeed,
                    "trending",
                    lambda: _query_ordered_products(
                        admin,
                        order_col="view_count",
                        limit=_SUB_FEED_LIMIT * 3,
                        exclude_set=set(),
                    ),
                )
                fut_premium = pool.submit(
                    _cached_guest_subfeed,
                    "premium",
                    lambda: _query_ordered_products(
                        admin,
                        order_col="listing_score",
                        limit=_SUB_FEED_LIMIT * 3,
                        exclude_set=set(),
                    ),
                )
            else:
                fut_trending = pool.submit(
                    _query_ordered_products,
                    admin,
                    order_col="view_count",
                    limit=_SUB_FEED_LIMIT * 3,
                    exclude_set=exclude_set,
                )
                fut_premium = pool.submit(
                    _query_ordered_products,
                    admin,
                    order_col="listing_score",
                    limit=_SUB_FEED_LIMIT * 3,
                    exclude_set=exclude_set,
                )

        algorithm_paged = fut_algo.result()
        fresh_raw = fut_fresh.result() if fut_fresh else []
        trending_raw = fut_trending.result() if fut_trending else []
        premium_raw = fut_premium.result() if fut_premium else []

    if want_subfeeds and is_guest and exclude_set:
        trending_raw = [p for p in trending_raw if str(p.id) not in exclude_set][:_SUB_FEED_LIMIT]
        premium_raw = [p for p in premium_raw if str(p.id) not in exclude_set][:_SUB_FEED_LIMIT]

    if want_subfeeds and not trending_raw:
        trending_raw = algorithm_paged[:_SUB_FEED_LIMIT]
    if want_subfeeds and not premium_raw:
        premium_raw = algorithm_paged[:_SUB_FEED_LIMIT]

    all_products = algorithm_paged + trending_raw + premium_raw + fresh_raw
    shop_ids = list({str(p.shop_id) for p in all_products if p.shop_id})

    shops_map: dict[str, dict[str, Any]] = {}
    if shop_ids:
        try:
            shops_r = (
                admin.table("shops")
                .select(
                    "id,name,slug,logo_url,owner_id,whatsapp_number,"
                    "is_active,category,trust_score,trust_badges,available_now,location"
                )
                .in_("id", shop_ids)
                .execute()
            )
            for s in (shops_r.data or []):
                sid = str(s["id"])
                loc = s.get("location")
                badges = s.get("trust_badges") or []
                if not isinstance(badges, list):
                    badges = []
                loc_display = loc.get("display") if isinstance(loc, dict) else loc
                loc_lat = None
                loc_lng = None
                if isinstance(loc, dict):
                    try:
                        lat_v = loc.get("lat")
                        lng_v = loc.get("lng")
                        if lat_v is not None and lng_v is not None:
                            loc_lat = float(lat_v)
                            loc_lng = float(lng_v)
                    except (TypeError, ValueError):
                        loc_lat = None
                        loc_lng = None
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
                    "trust_badges": badges if badges else ["shop_listed"],
                    "available_now": bool(s.get("available_now", False)),
                    "location": loc_display,
                    "location_lat": loc_lat,
                    "location_lng": loc_lng,
                }
        except Exception as exc:
            logger.warning("home feed batch shop fetch failed: %s", exc)

    product_ids = [str(p.id) for p in all_products if p.id]
    boosted_ids: set[str] = set()
    like_counts: dict[str, int] = {}
    viewer_liked_ids: set[str] = set()
    avg_ratings: dict[str, float] = {}
    review_counts: dict[str, int] = {}

    def _boosts() -> set[str]:
        if not product_ids:
            return set()
        try:
            now_iso = __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ).isoformat()
            boosts_r = (
                admin.table("listing_boosts")
                .select("listing_id")
                .in_("listing_id", product_ids)
                .eq("active", True)
                .gte("ends_at", now_iso)
                .execute()
            )
            return {
                str(b["listing_id"])
                for b in (boosts_r.data or [])
                if b.get("listing_id")
            }
        except Exception as exc:
            logger.warning("home feed batch boost fetch failed: %s", exc)
            return set()

    def _ratings() -> tuple[dict[str, float], dict[str, int]]:
        if not product_ids:
            return {}, {}
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
            ratings: dict[str, float] = {}
            rcounts: dict[str, int] = {}
            for pid in product_ids:
                if counts.get(pid, 0) > 0:
                    ratings[pid] = round(sums[pid] / counts[pid], 2)
                else:
                    ratings[pid] = 0.0
                rcounts[pid] = counts.get(pid, 0)
            return ratings, rcounts
        except Exception as exc:
            logger.warning("home feed batch rating fetch failed: %s", exc)
            return {}, {}

    # Ratings + boosts are public card signals — always enrich (including guests).
    # Likes / viewer_liked stay auth-only for faster anonymous first paint.
    if product_ids:
        with ThreadPoolExecutor(max_workers=4) as pool:
            fb = pool.submit(_boosts)
            fr = pool.submit(_ratings)
            if not is_guest:
                def _likes() -> dict[str, int]:
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
                        return {pid: counts_raw.get(pid, 0) for pid in product_ids}
                    except Exception as exc:
                        logger.warning("home feed batch like-count fetch failed: %s", exc)
                        return {}

                def _viewer_likes() -> set[str]:
                    if not user_id:
                        return set()
                    try:
                        vr = (
                            admin.table("product_likes")
                            .select("product_id")
                            .eq("user_id", user_id)
                            .in_("product_id", product_ids)
                            .execute()
                        )
                        return {
                            str(row.get("product_id"))
                            for row in (vr.data or [])
                            if row.get("product_id")
                        }
                    except Exception as exc:
                        logger.warning("home feed viewer-liked fetch failed: %s", exc)
                        return set()

                fl = pool.submit(_likes)
                fv = pool.submit(_viewer_likes)
                like_counts = fl.result()
                viewer_liked_ids = fv.result()

            boosted_ids = fb.result()
            avg_ratings, review_counts = fr.result()

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
                "viewer_liked": (str(p.id) in viewer_liked_ids) if not is_guest else None,
                "listing_score": _safe_int(p.listing_score),
                "location_name": p.location_name,
                "listing_meta": getattr(p, "listing_meta", None)
                if isinstance(getattr(p, "listing_meta", None), dict)
                else (p.get("listing_meta") if isinstance(p, dict) and isinstance(p.get("listing_meta"), dict) else {}),
                "created_at": p.created_at,
                "stock_quantity": int(getattr(p, "stock_quantity", 0) or 0),
                "shop": shop,
                "boosted": str(p.id) in boosted_ids,
                "average_rating": avg_ratings.get(str(p.id), 0.0),
                "review_count": review_counts.get(str(p.id), 0),
                "is_negotiable": getattr(p, "is_negotiable", True) is not False,
            })
        return out

    return {
        "algorithm": _embed(algorithm_paged),
        "trending": _embed(trending_raw) if want_subfeeds else [],
        "premium": _embed(premium_raw) if want_subfeeds else [],
        "fresh": _embed(fresh_raw) if want_subfeeds else [],
        "page": page,
        "limit": limit,
        "total": ((page - 1) * limit) + len(algorithm_paged),
        "has_more": len(algorithm_paged) == limit,
        "next_cursor": f"p:{page + 1}" if len(algorithm_paged) == limit else None,
    }
