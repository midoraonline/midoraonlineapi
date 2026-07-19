from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from supabase import Client

from core.authz import ensure_shop_owner
from core.schemas import PaginationParams
from db.supabase import get_supabase_client
from tenants.schemas import ShopResponse, ShopUpdate
from tenants import service as tenants_service
from core.security import get_current_user_id, get_optional_user_id

router = APIRouter()


@router.get("/me")
async def list_my_shops(
    client: Annotated[Client, Depends(get_supabase_client)],
    params: Annotated[PaginationParams, Depends()],
    user_id: str = Depends(get_current_user_id),
):
    result = tenants_service.list_my_shops(client, owner_id=user_id, page=params.page, limit=params.limit)
    return result


@router.get("/{shop_id}", response_model=ShopResponse)
async def get_shop(
    shop_id: str,
    client: Annotated[Client, Depends(get_supabase_client)],
    viewer_id: str | None = Depends(get_optional_user_id),
):
    shop = tenants_service.get_shop(client, shop_id, viewer_id=viewer_id)
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")
    return shop


@router.patch("/{shop_id}", response_model=ShopResponse)
async def update_shop(
    shop_id: str,
    body: ShopUpdate,
    client: Annotated[Client, Depends(get_supabase_client)],
    user_id: str = Depends(get_current_user_id),
):
    try:
        ensure_shop_owner(client, shop_id, user_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Shop not found")
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    shop = tenants_service.update_shop(client, shop_id, body, viewer_id=user_id)
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")
    return shop


@router.post("/{shop_id}/logo/generate")
async def generate_shop_logo(
    shop_id: str,
    client: Annotated[Client, Depends(get_supabase_client)],
    user_id: str = Depends(get_current_user_id),
):
    """Generate simple logo via Nano Banana for shop without logo. Merchant only."""
    try:
        ensure_shop_owner(client, shop_id, user_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Shop not found")
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return {"logo_url": ""}


@router.post("/{shop_id}/toggle-availability", response_model=ShopResponse)
async def toggle_shop_availability(
    shop_id: str,
    client: Annotated[Client, Depends(get_supabase_client)],
    user_id: str = Depends(get_current_user_id),
):
    """Refresh shop available_now from the owner's online presence (not a manual toggle)."""
    try:
        ensure_shop_owner(client, shop_id, user_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Shop not found")
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    from db.supabase import get_supabase_admin
    from marketplace.presence_service import sync_shop_availability_from_presence

    admin = get_supabase_admin()
    sync_shop_availability_from_presence(admin, user_id)

    shop = tenants_service.get_shop(client, shop_id, viewer_id=user_id)
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")
    return shop
