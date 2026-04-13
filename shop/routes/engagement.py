from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from db.supabase import get_supabase_client
from core.security import get_current_user_id, get_optional_user_id
from shop import engagement_service
from shop.schemas import ShopEngagementState, ViewCountResponse

router = APIRouter()


@router.get("/{shop_id}/engagement", response_model=ShopEngagementState)
async def get_shop_engagement(
    shop_id: str,
    client: Annotated[any, Depends(get_supabase_client)],
    viewer_id: str | None = Depends(get_optional_user_id),
):
    if not engagement_service.shop_exists(client, shop_id):
        raise HTTPException(status_code=404, detail="Shop not found")
    return engagement_service.get_shop_engagement(client, shop_id, viewer_id)


@router.post("/{shop_id}/follow", response_model=ShopEngagementState)
async def follow_shop(
    shop_id: str,
    client: Annotated[any, Depends(get_supabase_client)],
    user_id: str = Depends(get_current_user_id),
):
    try:
        return engagement_service.follow_shop(client, user_id, shop_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/{shop_id}/follow", response_model=ShopEngagementState)
async def unfollow_shop(
    shop_id: str,
    client: Annotated[any, Depends(get_supabase_client)],
    user_id: str = Depends(get_current_user_id),
):
    return engagement_service.unfollow_shop(client, user_id, shop_id)


@router.post("/{shop_id}/like", response_model=ShopEngagementState)
async def like_shop(
    shop_id: str,
    client: Annotated[any, Depends(get_supabase_client)],
    user_id: str = Depends(get_current_user_id),
):
    try:
        return engagement_service.like_shop(client, user_id, shop_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{shop_id}/views", response_model=ViewCountResponse)
async def record_shop_view(
    shop_id: str,
    client: Annotated[any, Depends(get_supabase_client)],
):
    """Increment shop page view (click) count. Call once when a customer opens the storefront."""
    try:
        n = engagement_service.record_shop_view(client, shop_id)
        return ViewCountResponse(view_count=n)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/{shop_id}/like", response_model=ShopEngagementState)
async def unlike_shop(
    shop_id: str,
    client: Annotated[any, Depends(get_supabase_client)],
    user_id: str = Depends(get_current_user_id),
):
    return engagement_service.unlike_shop(client, user_id, shop_id)

