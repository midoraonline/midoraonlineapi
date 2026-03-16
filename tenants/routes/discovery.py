from typing import Annotated

from fastapi import APIRouter, Depends

from core.schemas import PaginationParams
from db.supabase import get_supabase_client
from tenants import service as tenants_service

router = APIRouter()


@router.get("/")
async def list_shops(
    client: Annotated[any, Depends(get_supabase_client)],
    params: Annotated[PaginationParams, Depends()],
    search: str | None = None,
    shop_type: str | None = None,
):
    return tenants_service.list_shops(
        client, page=params.page, limit=params.limit, search=search, shop_type=shop_type
    )


@router.get("/by-slug/{slug}")
async def get_shop_by_slug(
    slug: str,
    client: Annotated[any, Depends(get_supabase_client)],
):
    from fastapi import HTTPException
    shop = tenants_service.get_shop_by_slug(client, slug)
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")
    return shop
