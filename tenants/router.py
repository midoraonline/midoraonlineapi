from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from supabase import Client

from auth import service as auth_service
from auth.cookies import set_auth_cookies
from core.schemas import PaginationParams
from core.security import get_current_user_id
from db.supabase import get_supabase_client
from tenants import service as tenants_service
from tenants.routes import discovery, shops, verifications
from tenants.schemas import ShopCreate, ShopResponse

router = APIRouter(prefix="/shops", tags=["shops"])


@router.get("", include_in_schema=True)
@router.get("/", include_in_schema=False)
async def list_shops(
    client: Annotated[Client, Depends(get_supabase_client)],
    params: Annotated[PaginationParams, Depends()],
    search: str | None = None,
    shop_type: str | None = None,
):
    """Public shop directory. Both /api/v1/shops and /api/v1/shops/ are supported."""
    return tenants_service.list_shops(
        client, page=params.page, limit=params.limit, search=search, shop_type=shop_type
    )


@router.post("", response_model=ShopResponse)
@router.post("/", response_model=ShopResponse, include_in_schema=False)
async def create_shop(
    body: ShopCreate,
    client: Annotated[Client, Depends(get_supabase_client)],
    request: Request,
    response: Response,
    user_id: str = Depends(get_current_user_id),
):
    """Create a shop. Both /api/v1/shops and /api/v1/shops/ accept POST
    (avoids 405 when proxies strip the trailing slash)."""
    try:
        shop = tenants_service.create_shop(client, user_id, body)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Auto-promote customer → merchant; refresh cookies so JWT role matches.
    role_changed = bool(shop.pop("_role_changed", False))
    new_role = str(shop.pop("_owner_role", "")) or None
    if role_changed and new_role:
        access, refresh = auth_service.create_access_and_refresh_tokens(
            user_id,
            new_role,
            user_agent=request.headers.get("user-agent"),
            ip=request.client.host if request.client else None,
        )
        set_auth_cookies(
            response,
            access_token=access,
            refresh_token=refresh,
            access_ttl_seconds=auth_service.access_ttl_seconds(),
            refresh_ttl_seconds=auth_service.refresh_ttl_seconds(),
        )

    return shop


router.include_router(shops.router)
router.include_router(discovery.router)
router.include_router(verifications.router)
