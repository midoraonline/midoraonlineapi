"""Feed service with per-user personalization.

Algorithm feed scores each product based on:
  - Category match  (+30) — recently viewed/liked products' categories
  - Search match    (+20) — recent search terms in title/description
  - Freshness       (+10) — created within last 7 days
  - Engagement      (listing_score from DB)
  - Boosted         (+15) — active boost
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from db.supabase import Client
from shop.schemas import ProductResponse

logger = logging.getLogger(__name__)

FRESHNESS_DAYS = 7


def _active_products_query(client: Client) -> Any:
    return client.table("products").select("*").eq("status", "active").eq("is_published", True)


def get_latest_feed(client: Client, limit: int = 20) -> list[ProductResponse]:
    """Fetch the latest active products sorted by creation date."""
    try:
        resp = (
            _active_products_query(client)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return [ProductResponse(**item) for item in resp.data]
    except Exception as exc:
        logger.warning("get_latest_feed failed, falling back: %s", exc)
        resp = (
            client.table("products")
            .select("*")
            .eq("is_published", True)
            .eq("status", "active")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return [ProductResponse(**item) for item in resp.data]


def _collect_user_signals(client: Client, user_id: str | None) -> dict[str, Any]:
    """Gather user preference signals for personalization."""
    signals: dict[str, Any] = {
        "categories": set(),
        "search_terms": [],
        "liked_product_ids": set(),
        "viewed_product_ids": set(),
    }

    if not user_id:
        return signals

    # Liked products and their categories
    try:
        likes_resp = (
            client.table("product_likes")
            .select("product_id")
            .eq("user_id", user_id)
            .limit(50)
            .execute()
        )
        liked_ids = [item["product_id"] for item in (likes_resp.data or [])]
        signals["liked_product_ids"] = set(liked_ids)
        if liked_ids:
            cat_resp = (
                client.table("products")
                .select("category")
                .in_("id", liked_ids)
                .execute()
            )
            for p in (cat_resp.data or []):
                if p.get("category"):
                    signals["categories"].add(p["category"])
    except Exception as exc:
        logger.warning("Failed to collect likes: %s", exc)

    # Recently viewed products and their categories
    try:
        viewed_resp = (
            client.table("listing_events")
            .select("listing_id")
            .eq("buyer_id", user_id)
            .eq("event_type", "viewed")
            .order("created_at", desc=True)
            .limit(20)
            .execute()
        )
        viewed_ids = [str(item["listing_id"]) for item in (viewed_resp.data or [])]
        signals["viewed_product_ids"] = set(viewed_ids)
        if viewed_ids:
            cat_resp = (
                client.table("products")
                .select("category")
                .in_("id", viewed_ids)
                .execute()
            )
            for p in (cat_resp.data or []):
                if p.get("category"):
                    signals["categories"].add(p["category"])
    except Exception as exc:
        logger.warning("Failed to collect views: %s", exc)

    # Recent search history
    try:
        search_resp = (
            client.table("search_history")
            .select("query")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(5)
            .execute()
        )
        seen = set()
        for s in (search_resp.data or []):
            q = s.get("query", "").strip().lower()
            if q and q not in seen:
                signals["search_terms"].append(q)
                seen.add(q)
    except Exception as exc:
        logger.warning("Failed to collect search history: %s", exc)

    return signals


def _product_score(
    product: dict[str, Any],
    signals: dict[str, Any],
    boosted_ids: set[str],
    cutoff: datetime,
) -> float:
    """Compute a personalisation score for a single product."""
    score = 0.0

    cat = (product.get("category") or "").strip()
    pid = str(product.get("id", ""))

    # Category match (+30)
    if cat and cat in signals["categories"]:
        score += 30

    # Search term match (+20 per term)
    title = (product.get("title") or "").lower()
    desc = (product.get("description") or "").lower()
    for term in signals["search_terms"]:
        if term in title or term in desc:
            score += 20

    # Freshness (+10)
    created_str = product.get("created_at")
    if created_str:
        try:
            created = datetime.fromisoformat(created_str)
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if created >= cutoff:
                score += 10
        except (ValueError, TypeError):
            pass

    # Engagement score from DB
    score += float(product.get("listing_score") or 0)

    # Boosted (+15)
    if pid in boosted_ids:
        score += 15

    # Already liked/viewed: small demotion to avoid repetition (-5)
    if pid in signals["liked_product_ids"] or pid in signals["viewed_product_ids"]:
        score -= 5

    return score


def get_algorithm_feed(
    client: Client,
    user_id: str | None = None,
    limit: int = 20,
) -> list[ProductResponse]:
    """Personalized algorithmic feed using scoring signals."""
    signals = _collect_user_signals(client, user_id)
    cutoff = datetime.now(timezone.utc) - timedelta(days=FRESHNESS_DAYS)

    query = _active_products_query(client)

    try:
        resp = query.execute()
    except Exception as exc:
        logger.warning("get_algorithm_feed query failed: %s", exc)
        return []

    products = resp.data or []

    # Collect boosted IDs for boost scoring
    boosted_ids: set[str] = set()
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        boosts_r = (
            client.table("listing_boosts")
            .select("listing_id")
            .in_("listing_id", [str(p["id"]) for p in products if p.get("id")])
            .eq("active", True)
            .gte("ends_at", now_iso)
            .execute()
        )
        boosted_ids = {str(b["listing_id"]) for b in (boosts_r.data or []) if b.get("listing_id")}
    except Exception as exc:
        logger.warning("Boost fetch failed: %s", exc)

    # Score and sort
    scored = [
        (p, _product_score(p, signals, boosted_ids, cutoff))
        for p in products
    ]
    scored.sort(key=lambda x: -x[1])

    return [ProductResponse(**p) for p, _ in scored[:limit]]
