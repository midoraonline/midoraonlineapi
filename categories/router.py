from fastapi import APIRouter, Depends, HTTPException

from categories.schemas import (
    CategoryCountsResponse,
    CategoryFieldsResponse,
    CategoryItem,
    CategoryListResponse,
)
from categories import service as categories_service
from categories.service import fallback_categories
from db.supabase import get_supabase_client

router = APIRouter(prefix="/categories", tags=["categories"])


@router.get("", response_model=CategoryListResponse)
@router.get("/", response_model=CategoryListResponse)
def list_categories_route(client=Depends(get_supabase_client)) -> CategoryListResponse:
    rows = categories_service.list_categories(client) if client else fallback_categories()
    if not rows:
        rows = fallback_categories()
    items = [CategoryItem(**row) for row in rows]
    return CategoryListResponse(items=items)



@router.get("/counts", response_model=CategoryCountsResponse)
def category_listing_counts_route(
    client=Depends(get_supabase_client),
) -> CategoryCountsResponse:
    if not client:
        return CategoryCountsResponse(counts={})
    return CategoryCountsResponse(
        counts=categories_service.listing_counts_by_parent(client),
    )


@router.get("/{slug}/fields", response_model=CategoryFieldsResponse)
def category_effective_fields_route(slug: str, client=Depends(get_supabase_client)):
    """Merged field definitions for Post Item (parent fields plus subcategory overrides)."""
    if not client:
        raise HTTPException(status_code=404, detail="Category not found")
    row = categories_service.effective_fields_for(client, slug)
    if not row:
        raise HTTPException(
            status_code=404,
            detail={"detail": "Category not found", "code": "category_not_found"},
        )
    return row


@router.get("/{slug}", response_model=CategoryItem)
def get_category_route(slug: str, client=Depends(get_supabase_client)):
    """One category or subcategory, including field definitions stored on it."""
    if not client:
        raise HTTPException(status_code=404, detail="Category not found")
    row = categories_service.get_category(client, slug)
    if not row:
        raise HTTPException(
            status_code=404,
            detail={"detail": "Category not found", "code": "category_not_found"},
        )
    return row