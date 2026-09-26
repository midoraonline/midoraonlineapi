from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from categories.fields import FieldEditError
from categories.schemas import (
    CategoryCreateRequest,
    CategoryUpdateRequest,
    FieldDefinitionWrite,
    FieldReorderRequest,
)
from categories.service import (
    add_category_field,
    delete_category_field,
    invalidate_categories_cache,
    metadata_for_storage,
    present_categories,
    reorder_category_fields,
    update_category_field,
)
from db.supabase import get_supabase_admin

router = APIRouter()


def _edit_error(exc: FieldEditError) -> HTTPException:
    return HTTPException(
        status_code=exc.status,
        detail={"detail": str(exc), "code": exc.code},
    )


def _rows() -> list[dict[str, Any]]:
    admin = get_supabase_admin()
    r = admin.table("categories").select("*").order("sort_order").execute()
    return present_categories(r.data or [])


@router.get("/settings/categories")
async def admin_get_categories() -> list[dict[str, Any]]:
    """Admin: list categories with normalized field definitions."""
    return _rows()


@router.get("/settings/categories/{slug}")
async def admin_get_category(slug: str) -> dict[str, Any]:
    for item in _rows():
        if item.get("slug") == slug:
            return item
    raise HTTPException(status_code=404, detail={"detail": "Category not found", "code": "category_not_found"})


@router.post("/settings/categories")
async def admin_create_category(body: CategoryCreateRequest) -> dict[str, Any]:
    """Admin: create a category or subcategory, including its own fields."""
    admin = get_supabase_admin()
    payload = {
        "slug": body.slug,
        "label": body.label,
        "parent_slug": body.parent_slug,
        "sort_order": body.sort_order,
        "metadata": metadata_for_storage([field.model_dump() for field in body.metadata]),
    }
    r = admin.table("categories").insert(payload).execute()
    if not r.data:
        raise HTTPException(status_code=400, detail="Failed to create category")
    invalidate_categories_cache()
    created = next((item for item in _rows() if item.get("slug") == body.slug), None)
    return created or r.data[0]


@router.patch("/settings/categories/{slug}")
async def admin_update_category(slug: str, body: CategoryUpdateRequest) -> dict[str, Any]:
    """Admin: update label, parent, sort order, or replace field definitions."""
    admin = get_supabase_admin()
    payload: dict[str, Any] = {}
    if body.label is not None:
        payload["label"] = body.label
    if body.parent_slug is not None:
        payload["parent_slug"] = body.parent_slug
    if body.sort_order is not None:
        payload["sort_order"] = body.sort_order
    if body.metadata is not None:
        payload["metadata"] = metadata_for_storage([field.model_dump() for field in body.metadata])

    if not payload:
        raise HTTPException(status_code=400, detail="No fields to update")

    r = admin.table("categories").update(payload).eq("slug", slug).execute()
    if not r.data:
        raise HTTPException(status_code=404, detail="Category not found")
    invalidate_categories_cache()
    updated = next((item for item in _rows() if item.get("slug") == slug), None)
    return updated or r.data[0]


@router.put("/settings/categories/{slug}/fields/order")
async def admin_reorder_fields(slug: str, body: FieldReorderRequest) -> dict[str, Any]:
    try:
        return reorder_category_fields(get_supabase_admin(), slug, body.keys)
    except FieldEditError as exc:
        raise _edit_error(exc) from exc


@router.post("/settings/categories/{slug}/fields")
async def admin_add_field(slug: str, body: FieldDefinitionWrite) -> dict[str, Any]:
    try:
        return add_category_field(get_supabase_admin(), slug, body.model_dump(exclude_unset=True))
    except FieldEditError as exc:
        raise _edit_error(exc) from exc


@router.patch("/settings/categories/{slug}/fields/{key}")
async def admin_update_field(slug: str, key: str, body: FieldDefinitionWrite) -> dict[str, Any]:
    try:
        return update_category_field(
            get_supabase_admin(), slug, key, body.model_dump(exclude_unset=True)
        )
    except FieldEditError as exc:
        raise _edit_error(exc) from exc


@router.delete("/settings/categories/{slug}/fields/{key}")
async def admin_delete_field(slug: str, key: str) -> dict[str, Any]:
    try:
        return delete_category_field(get_supabase_admin(), slug, key)
    except FieldEditError as exc:
        raise _edit_error(exc) from exc


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
