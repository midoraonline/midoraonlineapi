from typing import Annotated

from fastapi import APIRouter, Depends

from db.supabase import get_supabase_client
from core.security import get_current_user_id
from payments import service as payments_service
from payments.schemas import SubscribeRequest

router = APIRouter()


@router.post("/subscribe")
async def create_subscription(
    body: SubscribeRequest,
    user_id: str = Depends(get_current_user_id),
):
    result = payments_service.create_subscription_intent(
        shop_id=body.shop_id,
        amount=body.amount,
        currency=body.currency,
    )
    return result


@router.get("/subscriptions")
async def list_subscriptions(
    client: Annotated[any, Depends(get_supabase_client)],
    user_id: str = Depends(get_current_user_id),
):
    return payments_service.list_subscriptions_for_user(client)
