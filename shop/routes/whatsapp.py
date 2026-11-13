import asyncio
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from supabase import Client

from common.verification_service import (
    VerificationError,
    confirm_verification_code,
    http_status_for,
    send_verification_code,
)
from core.authz import ensure_shop_owner
from core.rate_limit import RateLimitWhatsAppCode
from core.security import get_current_user_id
from db.supabase import get_supabase_admin, get_supabase_client
from tenants import service as tenants_service
from tenants.schemas import ShopResponse

router = APIRouter()


class ConfirmShopWhatsAppCodeRequest(BaseModel):
    code: str = Field(..., min_length=4, max_length=8)


def _owner_or_404(client: Client, shop_id: str, user_id: str) -> None:
    try:
        ensure_shop_owner(client, shop_id, user_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Shop not found")
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


def _saved_number(shop_id: str) -> tuple[str, bool]:
    row = (
        get_supabase_admin()
        .table("shops")
        .select("whatsapp_number,whatsapp_verified")
        .eq("id", shop_id)
        .limit(1)
        .execute()
        .data
        or [{}]
    )[0]
    number = (row.get("whatsapp_number") or "").strip()
    if not number:
        raise HTTPException(
            status_code=400,
            detail={"detail": "Add a WhatsApp number first.", "code": "whatsapp_required"},
        )
    return number, bool(row.get("whatsapp_verified"))


def _raise(exc: VerificationError) -> None:
    raise HTTPException(
        status_code=http_status_for(exc.code),
        detail={"detail": str(exc), "code": exc.code},
    ) from exc


@router.post("/{shop_id}/whatsapp/send-code")
async def send_shop_whatsapp_code(
    shop_id: str,
    client: Annotated[Client, Depends(get_supabase_client)],
    _: RateLimitWhatsAppCode,
    user_id: str = Depends(get_current_user_id),
):
    """Send a code to the shop's saved WhatsApp number via the official Cloud API."""
    _owner_or_404(client, shop_id, user_id)
    number, verified = _saved_number(shop_id)
    if verified:
        return {"message": "Already verified", "verified": True}
    try:
        await asyncio.to_thread(
            send_verification_code,
            user_id=user_id,
            phone_number=number,
            purpose="whatsapp",
            channel="whatsapp",
            target_type="shop",
            target_id=shop_id,
        )
    except VerificationError as exc:
        _raise(exc)
    return {"message": "Verification code sent on WhatsApp", "verified": False}


@router.post("/{shop_id}/whatsapp/verify", response_model=ShopResponse)
async def verify_shop_whatsapp_code(
    shop_id: str,
    body: ConfirmShopWhatsAppCodeRequest,
    client: Annotated[Client, Depends(get_supabase_client)],
    _: RateLimitWhatsAppCode,
    user_id: str = Depends(get_current_user_id),
):
    """Confirm the code. The number must still be the one the code was sent to."""
    _owner_or_404(client, shop_id, user_id)
    number, _verified = _saved_number(shop_id)
    try:
        await asyncio.to_thread(
            confirm_verification_code,
            user_id=user_id,
            code=body.code,
            purpose="whatsapp",
            target_type="shop",
            target_id=shop_id,
            contact=number,
        )
    except VerificationError as exc:
        _raise(exc)

    get_supabase_admin().table("shops").update(
        {
            "whatsapp_verified": True,
            "whatsapp_registered": True,
            "whatsapp_checked_at": datetime.now(timezone.utc).isoformat(),
            "whatsapp_check_source": "otp",
        }
    ).eq("id", shop_id).eq("whatsapp_number", number).execute()
    tenants_service.revalidate_nextjs_cache_tag("shops")

    shop = tenants_service.get_shop(client, shop_id, viewer_id=user_id)
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")
    return shop
