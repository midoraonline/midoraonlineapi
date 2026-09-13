from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from categories.schemas import CategoryCreateRequest, CategoryUpdateRequest
from categories.service import invalidate_categories_cache
from db.supabase import get_supabase_admin

router = APIRouter()


@router.get("/settings/categories")
async def admin_get_categories() -> list[dict[str, Any]]:
    """Admin: list all categories including hierarchy and metadata."""
    admin = get_supabase_admin()
    r = admin.table("categories").select("*").order("sort_order").execute()
    return r.data or []


@router.post("/settings/categories")
async def admin_create_category(body: CategoryCreateRequest) -> dict[str, Any]:
    """Admin: create a new category or subcategory."""
    admin = get_supabase_admin()
    payload = {
        "slug": body.slug,
        "label": body.label,
        "parent_slug": body.parent_slug,
        "sort_order": body.sort_order,
        "metadata": [f.model_dump(exclude_none=True) for f in body.metadata],
    }
    r = admin.table("categories").insert(payload).execute()
    if not r.data:
        raise HTTPException(status_code=400, detail="Failed to create category")
    invalidate_categories_cache()
    return r.data[0]


@router.patch("/settings/categories/{slug}")
async def admin_update_category(slug: str, body: CategoryUpdateRequest) -> dict[str, Any]:
    """Admin: update a category's label, parent, sort order, or metadata fields."""
    admin = get_supabase_admin()
    payload: dict[str, Any] = {}
    if body.label is not None:
        payload["label"] = body.label
    if body.parent_slug is not None:
        payload["parent_slug"] = body.parent_slug
    if body.sort_order is not None:
        payload["sort_order"] = body.sort_order
    if body.metadata is not None:
        payload["metadata"] = [f.model_dump(exclude_none=True) for f in body.metadata]

    if not payload:
        raise HTTPException(status_code=400, detail="No fields to update")

    r = admin.table("categories").update(payload).eq("slug", slug).execute()
    if not r.data:
        raise HTTPException(status_code=404, detail="Category not found")
    invalidate_categories_cache()
    return r.data[0]


@router.delete("/settings/categories/{slug}")
async def admin_delete_category(slug: str) -> dict[str, Any]:
    """Admin: delete a category (fails if it still has subcategories)."""
    admin = get_supabase_admin()
    children = (
        admin.table("categories").select("slug").eq("parent_slug", slug).limit(1).execute()
    )
    if children.data:
        raise HTTPException(status_code=400, detail="Delete or move subcategories first")
    r = admin.table("categories").delete().eq("slug", slug).execute()
    if not r.data:
        raise HTTPException(status_code=404, detail="Category not found")
    invalidate_categories_cache()
    return {"status": "deleted", "slug": slug}
