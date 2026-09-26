from typing import Any
from collections import Counter

from fastapi import HTTPException
from postgrest.exceptions import APIError

from categories.fields import (
    FieldEditError,
    delete_own_field,
    missing_required_fields,
    normalize_fields,
    present_categories,
    reorder_entries,
    stored_from_api,
    upsert_own_field,
)
from core.categories import normalize_category, parent_label_for, seed_rows


def _has_nested_categories(rows: list[dict]) -> bool:
    return any(row.get("parent_slug") for row in rows)


import time

_CAT_LIST_CACHE: tuple[float, list[dict]] | None = None
_CAT_COUNTS_CACHE: tuple[float, dict[str, int]] | None = None
_TTL = 60.0


def list_categories(client: Any) -> list[dict]:
    """Return categories with subcategories.

    Prefer DB rows when they include nested categories. If the table only has
    top-level parents (migration not applied / incomplete seed), fall back to
    the in-code catalog so pickers and filters always get subcategories.
    """
    global _CAT_LIST_CACHE
    now = time.time()
    if _CAT_LIST_CACHE and (now - _CAT_LIST_CACHE[0] < _TTL):
        return _CAT_LIST_CACHE[1]

    res: list[dict] | None = None
    try:
        r = (
            client.table("categories")
            .select("slug,label,sort_order,parent_slug,metadata")
            .order("sort_order")
            .execute()
        )
        if r.data and _has_nested_categories(r.data):
            res = _public_rows(r.data)
    except APIError:
        pass

    if not res:
        res = _public_rows(seed_rows())

    _CAT_LIST_CACHE = (now, res)
    return res


def _public_rows(rows: list[dict]) -> list[dict]:
    return [
        {
            "slug": row["slug"],
            "label": row["label"],
            "sort_order": int(row.get("sort_order") or 0),
            "parent_slug": row.get("parent_slug"),
            "metadata": row.get("metadata") or [],
            "fields": row.get("fields") or [],
            "effective_fields": row.get("effective_fields") or [],
        }
        for row in present_categories(rows)
    ]


def _category_rows(client: Any) -> list[dict]:
    r = client.table("categories").select("*").order("sort_order").execute()
    return r.data or []


def _presented_slug(client: Any, slug: str) -> dict:
    for item in present_categories(_category_rows(client)):
        if item.get("slug") == slug:
            return item
    raise FieldEditError(404, "category_not_found", "Category not found")


def _parent_fields(rows: list[dict], row: dict) -> list[dict]:
    parent_slug = row.get("parent_slug")
    if not parent_slug:
        return []
    parent = next((item for item in rows if item.get("slug") == parent_slug), None)
    if not parent:
        return []
    return normalize_fields(parent.get("metadata"))


def _save_metadata(client: Any, slug: str, metadata: list[dict]) -> dict:
    updated = client.table("categories").update({"metadata": metadata}).eq("slug", slug).execute()
    if not updated.data:
        raise FieldEditError(404, "category_not_found", "Category not found")
    invalidate_categories_cache()
    return _presented_slug(client, slug)


def find_presented_category(rows: list[dict], identifier: str) -> dict | None:
    ident = (identifier or "").strip()
    if not ident:
        return None
    for row in rows:
        if row.get("slug") == ident or row.get("label") == ident:
            return row
    try:
        label = normalize_category(ident)
    except ValueError:
        label = None
    if not label:
        return None
    for row in rows:
        if row.get("label") == label:
            return row
    return None


def get_category(client: Any, identifier: str) -> dict | None:
    return find_presented_category(list_categories(client), identifier)


def effective_fields_for(client: Any, identifier: str) -> dict | None:
    row = get_category(client, identifier)
    if not row:
        return None
    return {
        "slug": row["slug"],
        "label": row["label"],
        "parent_slug": row.get("parent_slug"),
        "fields": row.get("effective_fields") or [],
    }


def add_category_field(client: Any, slug: str, provided: dict) -> dict:
    rows = _category_rows(client)
    row = next((item for item in rows if item.get("slug") == slug), None)
    if not row:
        raise FieldEditError(404, "category_not_found", "Category not found")
    key = provided.get("key") or provided.get("label") or ""
    metadata = upsert_own_field(
        row.get("metadata"),
        key=str(key),
        provided=provided,
        parent_fields=_parent_fields(rows, row),
        creating=True,
    )
    return _save_metadata(client, slug, metadata)


def update_category_field(client: Any, slug: str, key: str, provided: dict) -> dict:
    rows = _category_rows(client)
    row = next((item for item in rows if item.get("slug") == slug), None)
    if not row:
        raise FieldEditError(404, "category_not_found", "Category not found")
    metadata = upsert_own_field(
        row.get("metadata"),
        key=key,
        provided=provided,
        parent_fields=_parent_fields(rows, row),
        creating=False,
    )
    return _save_metadata(client, slug, metadata)


def delete_category_field(client: Any, slug: str, key: str) -> dict:
    rows = _category_rows(client)
    row = next((item for item in rows if item.get("slug") == slug), None)
    if not row:
        raise FieldEditError(404, "category_not_found", "Category not found")
    metadata = delete_own_field(row.get("metadata"), key, _parent_fields(rows, row))
    return _save_metadata(client, slug, metadata)


def reorder_category_fields(client: Any, slug: str, keys: list[str]) -> dict:
    rows = _category_rows(client)
    row = next((item for item in rows if item.get("slug") == slug), None)
    if not row:
        raise FieldEditError(404, "category_not_found", "Category not found")
    return _save_metadata(client, slug, reorder_entries(row.get("metadata"), keys))


def assert_required_listing_fields(client: Any, category: str | None, listing_meta: Any) -> None:
    """Reject a published listing that omits required category fields."""
    if not category or not str(category).strip():
        return
    row = find_presented_category(list_categories(client), str(category))
    if not row:
        return
    missing = missing_required_fields(row.get("effective_fields") or [], listing_meta)
    if not missing:
        return
    labels = ", ".join(item["label"] for item in missing)
    raise HTTPException(
        status_code=422,
        detail={
            "detail": f"Missing required fields: {labels}",
            "code": "listing_fields_required",
            "missing_fields": missing,
        },
    )


def metadata_for_storage(fields: list[dict] | None) -> list[dict]:
    return stored_from_api(fields)


def invalidate_categories_cache() -> None:
    """Bust the in-process category list and count caches after an admin write."""
    global _CAT_LIST_CACHE, _CAT_COUNTS_CACHE
    _CAT_LIST_CACHE = None
    _CAT_COUNTS_CACHE = None


def fallback_categories() -> list[dict]:
    return _public_rows(seed_rows())


def listing_counts_by_parent(client: Any) -> dict[str, int]:
    """Count published listings per top-level parent category label."""
    global _CAT_COUNTS_CACHE
    now = time.time()
    if _CAT_COUNTS_CACHE and (now - _CAT_COUNTS_CACHE[0] < _TTL):
        return _CAT_COUNTS_CACHE[1]

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

    res = dict(counts)
    _CAT_COUNTS_CACHE = (now, res)
    return res