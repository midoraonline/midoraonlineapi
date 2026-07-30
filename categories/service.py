from typing import Any
from collections import Counter

from postgrest.exceptions import APIError

from core.categories import parent_label_for, seed_rows


def _has_nested_categories(rows: list[dict]) -> bool:
    return any(row.get("parent_slug") for row in rows)


def list_categories(client: Any) -> list[dict]:
    """Return categories with subcategories.

    Prefer DB rows when they include nested categories. If the table only has
    top-level parents (migration not applied / incomplete seed), fall back to
    the in-code catalog so pickers and filters always get subcategories.
    """
    try:
        r = (
            client.table("categories")
            .select("slug,label,sort_order,parent_slug")
            .order("sort_order")
            .execute()
        )
        if r.data and _has_nested_categories(r.data):
            return [
                {
                    "slug": row["slug"],
                    "label": row["label"],
                    "sort_order": int(row.get("sort_order") or 0),
                    "parent_slug": row.get("parent_slug"),
                }
                for row in r.data
            ]
    except APIError:
        pass

    return seed_rows()


def fallback_categories() -> list[dict]:
    return seed_rows()


def listing_counts_by_parent(client: Any) -> dict[str, int]:
    """Count published listings per top-level parent category label."""
    counts: Counter[str] = Counter()
    try:
        r = (
            client.table("products")
            .select("category")
            .eq("is_published", True)
            .execute()
        )
        for row in r.data or []:
            parent = parent_label_for(row.get("category"))
            if parent:
                counts[parent] += 1
    except APIError:
        return {}
    return dict(counts)