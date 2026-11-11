import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from supabase import Client

from core.authz import ensure_shop_owner
from core.rate_limit import RateLimitWhatsAppCheck
from core.security import get_current_user_id
from db.supabase import get_supabase_admin, get_supabase_client

router = APIRouter()


@router.post("/{shop_id}/whatsapp/check")
async def check_shop_whatsapp(
    shop_id: str,
    client: Annotated[Client, Depends(get_supabase_client)],
    _: RateLimitWhatsAppCheck,
    user_id: str = Depends(get_current_user_id),
):
    """Ask whether the shop's saved number is on WhatsApp (wa-checker, whatsapp-web.js).

    on_whatsapp / not_on_whatsapp are definite. unavailable means the checker could not answer
    (down, capped, or not configured); the number stays hidden and the owner can try again later.
    A number that is not on WhatsApp stays hidden and buyers use Midora messages.
    """
    try:
        ensure_shop_owner(client, shop_id, user_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Shop not found")
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    from shop import whatsapp_check

    row = (
        get_supabase_admin()
        .table("shops")
        .select("whatsapp_number,whatsapp_verified,whatsapp_registered")
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
    if row.get("whatsapp_verified") or row.get("whatsapp_registered") is True:
        return {"status": "on_whatsapp", "visible": True, "source": "stored"}

    registered, source = await asyncio.to_thread(whatsapp_check.lookup, number)
    if registered is None:
        return {"status": "unavailable", "visible": False, "source": source}
    await asyncio.to_thread(whatsapp_check.record_on_shop, shop_id, registered, source)
    if registered:
        return {"status": "on_whatsapp", "visible": True, "source": source}
    return {"status": "not_on_whatsapp", "visible": False, "source": source, "fallback": "in_app_messages"}
