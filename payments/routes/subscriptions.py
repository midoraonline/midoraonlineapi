from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from supabase import Client

from core.authz import ensure_shop_owner
from db.supabase import get_supabase_client
from core.security import get_current_user_id
from payments import service as payments_service
from payments.plans import list_plans
from payments.schemas import PlanResponse, SubscribeRequest

router = APIRouter()


@router.get("/plans", response_model=list[PlanResponse])
async def get_plans():
    return list_plans()


@router.post("/subscribe")
async def create_subscription(
    body: SubscribeRequest,
    client: Annotated[Client, Depends(get_supabase_client)],
    user_id: str = Depends(get_current_user_id),
):
    try:
        ensure_shop_owner(client, body.shop_id, user_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Shop not found")
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    try:
        result = payments_service.create_subscription_intent(
            shop_id=body.shop_id,
            plan_tier=body.plan_tier,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


@router.get("/subscriptions")
async def list_subscriptions(
    client: Annotated[Client, Depends(get_supabase_client)],
    user_id: str = Depends(get_current_user_id),
):
    return payments_service.list_subscriptions_for_user(client)
