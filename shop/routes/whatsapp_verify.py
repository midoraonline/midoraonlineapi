from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from supabase import Client

from core.authz import ensure_shop_owner
from core.security import get_current_user_id
from db.supabase import get_supabase_admin, get_supabase_client
from tenants import service as tenants_service
from tenants.schemas import ConfirmShopWhatsAppCodeRequest, SendShopWhatsAppCodeRequest, ShopResponse

router = APIRouter()


@router.post("/{shop_id}/whatsapp/send-code")
async def send_shop_whatsapp_code(
    shop_id: str,
    body: SendShopWhatsAppCodeRequest,
    client: Annotated[Client, Depends(get_supabase_client)],
    user_id: str = Depends(get_current_user_id),
):
    try:
        ensure_shop_owner(client, shop_id, user_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Shop not found")
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    from common.verification_service import send_verification_code

    try:
        send_verification_code(
            user_id=user_id,
            phone_number=body.whatsapp_number,
            purpose="whatsapp",
            channel="whatsapp",
            target_type="shop",
            target_id=shop_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"message": "Verification code sent"}


@router.post("/{shop_id}/whatsapp/verify", response_model=ShopResponse)
async def verify_shop_whatsapp_code(
    shop_id: str,
    body: ConfirmShopWhatsAppCodeRequest,
    client: Annotated[Client, Depends(get_supabase_client)],
    user_id: str = Depends(get_current_user_id),
):
    try:
        ensure_shop_owner(client, shop_id, user_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Shop not found")
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    from common.verification_service import confirm_verification_code

    try:
        whatsapp_number = confirm_verification_code(
            user_id=user_id,
            code=body.code,
            purpose="whatsapp",
            target_type="shop",
            target_id=shop_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    get_supabase_admin().table("shops").update(
        {"whatsapp_number": whatsapp_number, "whatsapp_verified": True}
    ).eq("id", shop_id).execute()

    shop = tenants_service.get_shop(client, shop_id, viewer_id=user_id)
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")
    return shop
