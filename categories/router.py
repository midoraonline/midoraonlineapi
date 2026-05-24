from fastapi import APIRouter, Depends

from categories.schemas import CategoryItem, CategoryListResponse
from categories import service as categories_service
from categories.service import fallback_categories
from db.supabase import get_supabase_client

router = APIRouter(prefix="/categories", tags=["categories"])


@router.get("/", response_model=CategoryListResponse)
def list_categories_route(client=Depends(get_supabase_client)) -> CategoryListResponse:
    rows = categories_service.list_categories(client) if client else fallback_categories()
    if not rows:
        rows = fallback_categories()
    items = [CategoryItem(**row) for row in rows]
    return CategoryListResponse(items=items)
