from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from auth import service as auth_service
from auth.cookies import set_auth_cookies
from core.authz import ensure_shop_owner
from core.schemas import PaginationParams
from db.supabase import get_supabase_client
from tenants.schemas import ShopCreate, ShopListItem, ShopResponse, ShopUpdate
from tenants import service as tenants_service
from core.security import get_current_user_id, get_optional_user_id

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
    request: Request,
    response: Response,
    user_id: str = Depends(get_current_user_id),
):
    try:
        shop = tenants_service.create_shop(client, user_id, body)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    # The service auto-promotes a `customer` to `merchant`. When that happens
    # we mint a fresh cookie pair so the JWT role claim matches the DB without
    # forcing the user to log out and back in.
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


@router.get("/{shop_id}", response_model=ShopResponse)
async def get_shop(
    shop_id: str,
    client: Annotated[any, Depends(get_supabase_client)],
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
    client: Annotated[any, Depends(get_supabase_client)],
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
    client: Annotated[any, Depends(get_supabase_client)],
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
    client: Annotated[any, Depends(get_supabase_client)],
    user_id: str = Depends(get_current_user_id),
):
    """Toggle whether the shop shows as 'Available now'. Owner only."""
    try:
        ensure_shop_owner(client, shop_id, user_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Shop not found")
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    current = client.table("shops").select("available_now").eq("id", shop_id).limit(1).execute()
    if not current.data:
        raise HTTPException(status_code=404, detail="Shop not found")

    new_val = not bool(current.data[0].get("available_now", False))
    r = client.table("shops").update({"available_now": new_val}).eq("id", shop_id).execute()
    if not r.data:
        raise HTTPException(status_code=404, detail="Shop not found")

    shop = tenants_service.get_shop(client, shop_id, viewer_id=user_id)
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")
    return shop
