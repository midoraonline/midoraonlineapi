from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from core.security import get_optional_user_id
from db.supabase import get_supabase_client
from tenants import service as tenants_service

router = APIRouter()


@router.get("/by-slug/{slug}")
async def get_shop_by_slug(
    slug: str,
    client: Annotated[any, Depends(get_supabase_client)],
    viewer_id: str | None = Depends(get_optional_user_id),
):
    shop = tenants_service.get_shop_by_slug(client, slug, viewer_id=viewer_id)
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")
    return shop
