from typing import Annotated

from fastapi import APIRouter, Depends

from core.schemas import PaginationParams
from db.supabase import get_supabase_client
from tenants import service as tenants_service
from tenants.routes import discovery, shops, verifications

router = APIRouter(prefix="/shops", tags=["shops"])


@router.get("", include_in_schema=True)
@router.get("/", include_in_schema=False)
async def list_shops(
    client: Annotated[any, Depends(get_supabase_client)],
    params: Annotated[PaginationParams, Depends()],
    search: str | None = None,
    shop_type: str | None = None,
):
    """Public shop directory. Both /api/v1/shops and /api/v1/shops/ are supported."""
    return tenants_service.list_shops(
        client, page=params.page, limit=params.limit, search=search, shop_type=shop_type
    )


router.include_router(shops.router)
router.include_router(discovery.router)
router.include_router(verifications.router)
