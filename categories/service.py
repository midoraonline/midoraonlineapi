from typing import Any

from postgrest.exceptions import APIError

from core.categories import CANONICAL_CATEGORIES, seed_rows


def list_categories(client: Any) -> list[dict]:
    """Return seeded categories from DB when available, else in-code catalog."""
    try:
        r = (
            client.table("categories")
            .select("slug,label,sort_order")
            .order("sort_order")
            .execute()
        )
        if r.data:
            return [
                {
                    "slug": row["slug"],
                    "label": row["label"],
                    "sort_order": int(row.get("sort_order") or 0),
                }
                for row in r.data
            ]
    except APIError:
        pass

    return seed_rows()


def fallback_categories() -> list[dict]:
    return [
        {"slug": c.slug, "label": c.label, "sort_order": c.sort_order}
        for c in CANONICAL_CATEGORIES
    ]
