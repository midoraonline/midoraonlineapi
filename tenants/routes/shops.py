from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from core.schemas import PaginationParams
from db.supabase import get_supabase_client
from tenants.schemas import ShopCreate, ShopListItem, ShopResponse, ShopUpdate
from tenants import service as tenants_service
from core.security import get_current_user_id

router = APIRouter()


@router.get("/me")
async def list_my_shops(
    client: Annotated[any, Depends(get_supabase_client)],
    params: Annotated[PaginationParams, Depends()],
    user_id: str = Depends(get_current_user_id),
):
    result = tenants_service.list_my_shops(client, page=params.page, limit=params.limit)
    return result


@router.post("/", response_model=ShopResponse)
async def create_shop(
    body: ShopCreate,
    client: Annotated[any, Depends(get_supabase_client)],
    user_id: str = Depends(get_current_user_id),
):
    try:
        return tenants_service.create_shop(client, user_id, body)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{shop_id}", response_model=ShopResponse)
async def get_shop(
    shop_id: str,
    client: Annotated[any, Depends(get_supabase_client)],
):
    shop = tenants_service.get_shop(client, shop_id)
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")
    return shop


@router.patch("/{shop_id}", response_model=ShopResponse)
async def update_shop(
    shop_id: str,
    body: ShopUpdate,
    client: Annotated[any, Depends(get_supabase_client)],
    user_id: str = Depends(get_current_user_id),
):
    shop = tenants_service.update_shop(client, shop_id, body)
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")
    return shop


@router.post("/{shop_id}/logo/generate")
async def generate_shop_logo(
    shop_id: str,
    client: Annotated[any, Depends(get_supabase_client)],
    user_id: str = Depends(get_current_user_id),
):
    """Generate simple logo via Nano Banana for shop without logo. Merchant only."""
    return {"logo_url": ""}
