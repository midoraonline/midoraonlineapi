from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from supabase import Client

from core.authz import ensure_shop_owner
from core.schemas import PaginationParams
from db.supabase import get_supabase_client
from core.security import get_current_user_id
from shop import service as shop_service
from shop.schemas import OrderCreate, OrderResponse, OrderUpdate

router = APIRouter()


@router.post("/", response_model=OrderResponse)
async def create_order(
  body: OrderCreate,
  client: Annotated[Client, Depends(get_supabase_client)],
  user_id: str = Depends(get_current_user_id),
):
    try:
        return shop_service.create_order(client, user_id, body)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/")
async def list_orders(
  client: Annotated[Client, Depends(get_supabase_client)],
  params: Annotated[PaginationParams, Depends()],
  user_id: str = Depends(get_current_user_id),
):
    # RLS: customer sees own orders; merchant sees orders for their shops
    return shop_service.list_orders(client, page=params.page, limit=params.limit, customer_id=user_id)


@router.patch("/{order_id}", response_model=OrderResponse)
async def update_order(
  order_id: str,
  body: OrderUpdate,
  client: Annotated[Client, Depends(get_supabase_client)],
  user_id: str = Depends(get_current_user_id),
):
    existing = client.table("orders").select("shop_id").eq("id", order_id).limit(1).execute()
    if not existing.data:
        raise HTTPException(status_code=404, detail="Order not found")
    shop_id = str(existing.data[0].get("shop_id") or "")
    try:
        ensure_shop_owner(client, shop_id, user_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Order not found")
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    order_status = body.order_status or "pending"
    updated = shop_service.update_order_status(client, order_id, order_status)
    if not updated:
        raise HTTPException(status_code=404, detail="Order not found")
    return updated
