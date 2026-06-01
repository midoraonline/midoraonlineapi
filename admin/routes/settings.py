from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from db.supabase import get_supabase_admin

router = APIRouter()


@router.get("/settings/categories")
async def admin_get_categories() -> list[dict[str, Any]]:
    """Admin: list all categories including hierarchy."""
    admin = get_supabase_admin()
    r = admin.table("categories").select("*").order("sort_order").execute()
    return r.data or []


@router.post("/settings/categories")
async def admin_create_category(
    slug: str = Query(...),
    label: str = Query(...),
    parent_slug: str | None = Query(None),
    sort_order: int = Query(0),
) -> dict[str, Any]:
    """Admin: create a new category."""
    admin = get_supabase_admin()
    payload = {
        "slug": slug,
        "label": label,
        "parent_slug": parent_slug,
        "sort_order": sort_order,
    }
    r = admin.table("categories").insert(payload).execute()
    if not r.data:
        return {"error": "Failed to create category"}
    return r.data[0]


@router.patch("/settings/categories/{slug}")
async def admin_update_category(
    slug: str,
    label: str | None = Query(None),
    parent_slug: str | None = Query(None),
    sort_order: int | None = Query(None),
) -> dict[str, Any]:
    """Admin: update a category."""
    admin = get_supabase_admin()
    payload = {}
    if label is not None:
        payload["label"] = label
    if parent_slug is not None:
        payload["parent_slug"] = parent_slug
    if sort_order is not None:
        payload["sort_order"] = sort_order

    if not payload:
        return {"error": "No fields to update"}

    r = admin.table("categories").update(payload).eq("slug", slug).execute()
    if not r.data:
        return {"error": "Category not found"}
    return r.data[0]


@router.delete("/settings/categories/{slug}")
async def admin_delete_category(slug: str) -> dict[str, Any]:
    """Admin: delete a category."""
    admin = get_supabase_admin()
    r = admin.table("categories").delete().eq("slug", slug).execute()
    if not r.data:
        return {"error": "Category not found"}
    return {"status": "deleted", "slug": slug}
