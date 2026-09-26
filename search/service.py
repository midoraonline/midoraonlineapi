"""Semantic product search using stored embeddings and search_history analytics."""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from datetime import datetime, timezone, timedelta
from typing import Any

from feed.catalog import ListingFilters, fetch_catalog_page, sort_rows, rating_map, trust_map
from feed.embeddings import cosine_similarity, embed_query, parse_embedding

logger = logging.getLogger(__name__)

MIN_QUERY_LEN = 2
MAX_SEARCH_POOL = 2000
SEARCH_BATCH = 500

_PRODUCT_FIELDS = (
    "id,shop_id,title,description,category,item_type,price_ugx,discount_price,discount_expires_at,image_urls,"
    "listing_score,location_name,view_count,created_at,embedding,listing_meta,is_negotiable,stock_quantity"
)


def _normalize_query(query: str) -> str:
    return re.sub(r"\s+", " ", query.strip())


def _coerce_images(image_urls: Any) -> list[str]:
    if isinstance(image_urls, list):
        return [str(x) for x in image_urls if x]
    if isinstance(image_urls, str):
        return [s.strip() for s in image_urls.split(",") if s.strip()]
    return []


def log_search(
    client: Any,
    query: str,
    *,
    user_id: str | None = None,
    result_count: int = 0,
    search_mode: str = "vector",
) -> None:
    """Persist a search query to search_history for personalization and analytics."""
    q = _normalize_query(query)
    if len(q) < MIN_QUERY_LEN:
        return

    payload: dict[str, Any] = {
        "query": q,
        "result_count": result_count,
        "search_mode": search_mode,
    }
    if user_id:
        payload["user_id"] = user_id

    try:
        client.table("search_history").insert(payload).execute()
        if user_id:
            # Search intent is a strong personalization signal.
            from feed.service import invalidate_user_feed_cache
            invalidate_user_feed_cache(client, user_id)
    except Exception as exc:
        logger.warning("log_search failed: %s", exc)


def get_recent_searches(client: Any, user_id: str, *, limit: int = 10) -> list[dict[str, Any]]:
    """Return the user's most recent unique search queries."""
    try:
        resp = (
            client.table("search_history")
            .select("query,created_at,result_count")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(limit * 5)
            .execute()
        )
    except Exception as exc:
        logger.warning("get_recent_searches failed: %s", exc)
        return []

    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in resp.data or []:
        q = _normalize_query(row.get("query") or "")
        key = q.lower()
        if len(q) < MIN_QUERY_LEN or key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "query": q,
                "searched_at": row.get("created_at"),
                "result_count": row.get("result_count"),
            }
        )
        if len(out) >= limit:
            break
    return out


def get_trending_searches(
    client: Any,
    *,
    limit: int = 10,
    days: int = 30,
) -> list[dict[str, Any]]:
    """Return the most frequent search queries in the recent window."""
    since = (datetime.now(timezone.utc) - timedelta(days=max(1, days))).isoformat()
    try:
        resp = (
            client.table("search_history")
            .select("query")
            .gte("created_at", since)
            .order("created_at", desc=True)
            .limit(5000)
            .execute()
        )
    except Exception as exc:
        logger.warning("get_trending_searches failed: %s", exc)
        return []

    counter: Counter[str] = Counter()
    for row in resp.data or []:
        q = _normalize_query(row.get("query") or "").lower()
        if len(q) >= MIN_QUERY_LEN:
            counter[q] += 1

    return [{"query": q, "count": count} for q, count in counter.most_common(limit)]


def _fetch_embedded_products(
    client: Any,
    *,
    filters: ListingFilters,
) -> list[dict[str, Any]]:
    from feed.catalog import apply_listing_filters, resolve_constraints

    from feed.catalog import _select_for

    resolved = resolve_constraints(client, filters)
    if resolved.impossible:
        return []
    select_cols = _select_for(_PRODUCT_FIELDS, resolved)
    products: list[dict[str, Any]] = []
    offset = 0

    while len(products) < MAX_SEARCH_POOL:
        query = (
            client.table("products")
            .select(select_cols)
            .eq("status", "active")
            .eq("is_published", True)
            .not_.is_("embedding", "null")
        )
        query = apply_listing_filters(query, filters, resolved)

        resp = query.range(offset, offset + SEARCH_BATCH - 1).execute()
        batch = resp.data or []
        if not batch:
            break
        products.extend(batch)
        if len(batch) < SEARCH_BATCH:
            break
        offset += SEARCH_BATCH

    return products[:MAX_SEARCH_POOL]


def _keyword_search(
    client: Any,
    query: str,
    *,
    filters: ListingFilters,
    page: int,
    limit: int,
) -> tuple[list[tuple[dict[str, Any], float]], int, str]:
    q = _normalize_query(query)
    safe = q.replace(",", " ").replace("%", "").replace("(", "").replace(")", "")

    def _refine(builder: Any) -> Any:
        if not safe:
            return builder
        return builder.or_(
            f"title.ilike.%{safe}%,description.ilike.%{safe}%,category.ilike.%{safe}%"
        )

    rows, total = fetch_catalog_page(
        client,
        filters,
        select=_PRODUCT_FIELDS,
        page=page,
        limit=limit,
        refine=_refine,
    )
    return [(row, 0.0) for row in rows], total, "keyword"


def _score_product(
    product: dict[str, Any],
    query: str,
    query_vector: list[float] | None,
) -> float:
    title = (product.get("title") or "").lower()
    desc = (product.get("description") or "").lower()
    cat = (product.get("category") or "").lower()
    q_lower = query.lower()

    score = 0.0

    product_vector = parse_embedding(product.get("embedding"))
    if query_vector and product_vector:
        sim = cosine_similarity(query_vector, product_vector)
        score += max(sim, 0.0) * 100.0

    if q_lower in title:
        score += 30.0
    elif any(term in title for term in q_lower.split() if len(term) >= 3):
        score += 15.0

    if q_lower in desc:
        score += 10.0

    if q_lower in cat:
        score += 20.0

    score += float(product.get("listing_score") or 0) * 0.05
    return score


def _vector_search(
    client: Any,
    query: str,
    *,
    filters: ListingFilters,
    limit: int,
    offset: int,
) -> tuple[list[tuple[dict[str, Any], float]], int, str]:
    query_vector = embed_query(query)
    if query_vector is None:
        return [], 0, "keyword"

    products = _fetch_embedded_products(client, filters=filters)
    if not products:
        return [], 0, "keyword"

    scored = [
        (product, score)
        for product in products
        if (score := _score_product(product, query, query_vector)) >= 12.0
    ]
    scored.sort(key=lambda item: -item[1])
    if filters.sort != "relevance":
        scored = _resort_scored(client, scored, filters.sort)
    total = len(scored)
    page = scored[offset : offset + limit]
    mode = "hybrid" if any(item[1] >= 30 for item in page) else "vector"
    return page, total, mode


def _attach_shops(client: Any, products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    shop_ids = list({str(p.get("shop_id")) for p in products if p.get("shop_id")})
    product_ids = [str(p.get("id")) for p in products if p.get("id")]
    shops_map: dict[str, dict[str, Any]] = {}
    if shop_ids:
        try:
            from shop.locations import shop_coordinates
            from shop.seller_display import overlay_personal_sellers

            resp = None
            for cols in (
                "id,name,slug,logo_url,owner_id,is_active,category,trust_score,location,"
                "available_now,whatsapp_number,trust_badges,is_personal,created_at,last_seen_at",
                "id,name,slug,logo_url,owner_id,is_active,category,trust_score,location,"
                "available_now,whatsapp_number,trust_badges",
            ):
                try:
                    resp = client.table("shops").select(cols).in_("id", shop_ids).execute()
                    break
                except Exception as exc:
                    logger.warning("search shop select failed: %s", exc)
                    resp = None
            for shop in (resp.data if resp else []) or []:
                sid = str(shop["id"])
                loc = shop.get("location")
                badges = shop.get("trust_badges") or []
                if not isinstance(badges, list):
                    badges = []
                loc_display = loc.get("display") if isinstance(loc, dict) else loc
                coords = shop_coordinates(loc)
                loc_lat = coords[0] if coords else None
                loc_lng = coords[1] if coords else None
                shops_map[sid] = {
                    "id": sid,
                    "name": shop.get("name", ""),
                    "slug": shop.get("slug", ""),
                    "logo_url": shop.get("logo_url"),
                    "owner_id": str(shop.get("owner_id", "")) if shop.get("owner_id") else None,
                    "is_active": bool(shop.get("is_active", False)),
                    "category": shop.get("category"),
                    "trust_score": int(shop.get("trust_score") or 0),
                    "location": loc_display,
                    "location_lat": loc_lat,
                    "location_lng": loc_lng,
                    "available_now": bool(shop.get("available_now", False)),
                    "whatsapp_number": shop.get("whatsapp_number"),
                    "trust_badges": badges if badges else ["shop_listed"],
                    "is_personal": bool(shop.get("is_personal")),
                    "created_at": shop.get("created_at"),
                    "last_seen_at": shop.get("last_seen_at"),
                }
            overlay_personal_sellers(client, shops_map)
        except Exception as exc:
            logger.warning("search shop batch fetch failed: %s", exc)

    avg_ratings: dict[str, float] = {}
    review_counts: dict[str, int] = {}
    if product_ids:
        try:
            rev_r = (
                client.table("product_reviews")
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
                review_counts[pid] = counts.get(pid, 0)
        except Exception as exc:
            logger.warning("search rating batch fetch failed: %s", exc)

    from shop.locations import listing_is_online
    from shop.serializers import clip_card_description

    out: list[dict[str, Any]] = []
    for product in products:
        pid = str(product.get("id", ""))
        sid = str(product.get("shop_id", ""))
        shop = dict(shops_map.get(sid) or {})
        online = listing_is_online(product.get("location_name"), product.get("listing_meta"))
        if online:
            shop["location_lat"] = None
            shop["location_lng"] = None
            shop["location"] = "Online"
        imgs = _coerce_images(product.get("image_urls"))
        out.append(
            {
                "id": pid,
                "shop_id": sid,
                "title": product.get("title", ""),
                "description": clip_card_description(product.get("description")),
                "price_ugx": float(product.get("price_ugx") or 0),
                "discount_price": float(product["discount_price"]) if product.get("discount_price") is not None else None,
                "discount_expires_at": str(product["discount_expires_at"]) if product.get("discount_expires_at") else None,
                "category": product.get("category"),
                "item_type": product.get("item_type"),
                "image_urls": imgs,
                "primary_image": imgs[0] if imgs else None,
                "listing_score": int(product.get("listing_score") or 0),
                "view_count": int(product.get("view_count") or 0),
                "location_name": product.get("location_name"),
                "is_online": online,
                "created_at": product.get("created_at"),
                "updated_at": product.get("created_at"),
                "average_rating": avg_ratings.get(pid, 0.0),
                "review_count": review_counts.get(pid, 0),
                "is_negotiable": product.get("is_negotiable") is not False,
                "stock_quantity": product.get("stock_quantity"),
                "listing_meta": product.get("listing_meta") if isinstance(product.get("listing_meta"), dict) else {},
                "shop": shops_map.get(sid) or {},
            }
        )
    return out


def _resort_scored(
    client: Any,
    scored: list[tuple[dict[str, Any], float]],
    sort: str,
) -> list[tuple[dict[str, Any], float]]:
    rows = [row for row, _ in scored]
    ratings = {}
    trusts = {}
    if sort == "best_rated":
        ratings = rating_map(client, [str(row.get("id")) for row in rows if row.get("id")])
    elif sort == "trust_score":
        trusts = trust_map(
            client,
            list({str(row.get("shop_id")) for row in rows if row.get("shop_id")}),
        )
    ordered = sort_rows(rows, sort, ratings=ratings, trusts=trusts)
    score_by_id = {str(row.get("id")): score for row, score in scored}
    return [(row, score_by_id.get(str(row.get("id")), 0.0)) for row in ordered]


def search_products(
    client: Any,
    query: str,
    *,
    page: int = 1,
    limit: int = 20,
    category: str | None = None,
    filters: ListingFilters | None = None,
    user_id: str | None = None,
    log: bool = True,
) -> dict[str, Any]:
    """Search products by semantic vector similarity with keyword fallback."""
    q = _normalize_query(query)
    if len(q) < MIN_QUERY_LEN:
        return {
            "items": [],
            "total": 0,
            "page": page,
            "limit": limit,
            "total_pages": 0,
            "query": q,
            "mode": "none",
        }

    if filters is None:
        filters = ListingFilters(category=(category or "").strip() or None)
    elif category and not filters.category:
        filters = ListingFilters(
            category=category.strip() or None,
            listing_type=filters.listing_type,
            verified_only=filters.verified_only,
            available=filters.available,
            min_price=filters.min_price,
            max_price=filters.max_price,
            min_rating=filters.min_rating,
            location=filters.location,
            lat=filters.lat,
            lng=filters.lng,
            radius_km=filters.radius_km,
            opportunity_kind=filters.opportunity_kind,
            compensation=filters.compensation,
            pricing_model=filters.pricing_model,
            sort=filters.sort,
        )

    limit = min(max(limit, 1), 100)
    offset = (page - 1) * limit

    # Keyword first so "head" matches Headphones. Vector only fills gaps —
    # it used to report total=all embeddings, which skipped keyword entirely.
    # Filters are inside the keyword query, before range.
    scored, total, mode = _keyword_search(
        client, q, filters=filters, page=page, limit=limit
    )
    if total == 0:
        scored, total, mode = _vector_search(
            client, q, filters=filters, limit=limit, offset=offset
        )

    products = [row for row, _ in scored]
    items = _attach_shops(client, products)
    for item, (_, score) in zip(items, scored):
        item["similarity_score"] = round(score, 4)

    total_pages = math.ceil(total / limit) if limit else 0

    if log:
        log_search(
            client,
            q,
            user_id=user_id,
            result_count=total,
            search_mode=mode,
        )

    return {
        "items": items,
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": total_pages,
        "query": q,
        "mode": mode,
    }
