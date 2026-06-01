from __future__ import annotations

import logging
import random
from typing import Any

from db.supabase import Client
from shop.schemas import ProductResponse

logger = logging.getLogger(__name__)


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
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return [ProductResponse(**item) for item in resp.data]


def get_algorithm_feed(client: Client, user_id: str | None = None, limit: int = 20) -> list[ProductResponse]:
    """
    Personalized algorithmic feed using listing_score as the primary ranking signal.
    Falls back to view_count if listing_score column doesn't exist yet.
    """
    preferred_categories: list[str] = []
    recent_searches: list[str] = []

    if user_id:
        likes_resp = (
            client.table("product_likes")
            .select("product_id")
            .eq("user_id", user_id)
            .limit(50)
            .execute()
        )
        if likes_resp.data:
            product_ids = [item["product_id"] for item in likes_resp.data]
            products_resp = (
                client.table("products")
                .select("category")
                .in_("id", product_ids)
                .execute()
            )
            for p in products_resp.data:
                if p.get("category"):
                    preferred_categories.append(p["category"])

        search_resp = (
            client.table("search_history")
            .select("query")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(3)
            .execute()
        )
        if search_resp.data:
            for s in search_resp.data:
                q = s.get("query", "").strip()
                if q and q not in recent_searches:
                    recent_searches.append(q)

    preferred_categories = list(set(preferred_categories))

    query = _active_products_query(client)

    if preferred_categories or recent_searches:
        or_conditions = []
        if preferred_categories:
            cats = ",".join([f'"{c}"' for c in preferred_categories])
            or_conditions.append(f"category.in.({cats})")

        for term in recent_searches:
            safe_term = term.replace(",", " ").replace('"', "")
            or_conditions.append(f"title.ilike.%{safe_term}%")
            or_conditions.append(f"description.ilike.%{safe_term}%")

        if or_conditions:
            query = query.or_(",".join(or_conditions))

        try:
            query = query.order("listing_score", desc=True).limit(limit * 2)
        except Exception:
            query = query.order("view_count", desc=True).limit(limit * 2)

        resp = query.execute()
        items = resp.data
        random.shuffle(items)
        return [ProductResponse(**item) for item in items[:limit]]

    try:
        query = query.order("listing_score", desc=True).limit(limit)
    except Exception:
        query = query.order("view_count", desc=True).limit(limit)

    resp = query.execute()
    return [ProductResponse(**item) for item in resp.data]
